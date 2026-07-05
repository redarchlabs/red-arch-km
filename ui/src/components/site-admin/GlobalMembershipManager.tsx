"use client";

import { UserMinus, UserPlus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { listAllUsers, type AdminUser } from "@/lib/api/adminUsers";
import { getApiErrorMessage } from "@/lib/api/errors";
import {
  deleteMembership,
  getMembershipForUser,
  listUsersInOrg,
  updateMembership,
  upsertMembership,
} from "@/lib/api/memberships";
import { listOrgs } from "@/lib/api/orgs";
import type { Org } from "@/types";
import { cn } from "@/lib/utils";

interface MemberRow {
  id: string;
  username: string;
  email: string;
}

/**
 * Org-centric membership management for site admins: pick any org, then add,
 * remove, or toggle org-admin on its members. Fine-grained dimension
 * assignment (regions/roles/…) stays in the org-scoped /admin Members tab.
 */
export function GlobalMembershipManager() {
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(null);
  const [members, setMembers] = useState<MemberRow[]>([]);
  const [adminIds, setAdminIds] = useState<Set<string>>(new Set());
  const [candidates, setCandidates] = useState<AdminUser[]>([]);
  const [candidateQuery, setCandidateQuery] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isMembersLoading, setIsMembersLoading] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [busyUserId, setBusyUserId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Guards against out-of-order responses when the operator switches orgs
  // faster than requests resolve.
  const loadSeqRef = useRef(0);

  useEffect(() => {
    void (async () => {
      try {
        const orgList = await listOrgs();
        setOrgs(orgList);
        setSelectedOrgId((current) => current ?? orgList[0]?.id ?? null);
      } catch (e: unknown) {
        setError(getApiErrorMessage(e, "Failed to load organizations"));
      } finally {
        setIsLoading(false);
      }
    })();
  }, []);

  const loadMembers = useCallback(async () => {
    if (!selectedOrgId) return;
    const seq = ++loadSeqRef.current;
    setIsMembersLoading(true);
    setError(null);
    try {
      const rows = await listUsersInOrg(selectedOrgId);
      // Membership records carry the org-admin flag; fetch them for badges.
      const memberships = await Promise.all(
        rows.map((row) => getMembershipForUser(row.id, selectedOrgId)),
      );
      if (seq !== loadSeqRef.current) return; // stale response — org changed
      setMembers(rows);
      setAdminIds(
        new Set(
          memberships
            .filter((m): m is NonNullable<typeof m> => m !== null && m.is_org_admin)
            .map((m) => m.profile_id),
        ),
      );
    } catch (e: unknown) {
      if (seq === loadSeqRef.current) {
        setError(getApiErrorMessage(e, "Failed to load members"));
      }
    } finally {
      if (seq === loadSeqRef.current) {
        setIsMembersLoading(false);
      }
    }
  }, [selectedOrgId]);

  useEffect(() => {
    // A stale candidate list from another org must never survive an org
    // switch — its not-a-member filter no longer applies.
    setCandidates([]);
    setCandidateQuery("");
    void loadMembers();
  }, [loadMembers]);

  const searchCandidates = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isSearching) return;
    setIsSearching(true);
    setError(null);
    try {
      const result = await listAllUsers({ q: candidateQuery.trim() || undefined, pageSize: 20 });
      const memberIds = new Set(members.map((m) => m.id));
      setCandidates(result.items.filter((u) => u.is_active && !memberIds.has(u.id)));
    } catch (err: unknown) {
      setError(getApiErrorMessage(err, "Search failed"));
    } finally {
      setIsSearching(false);
    }
  };

  const addMember = async (user: AdminUser) => {
    if (!selectedOrgId) return;
    setBusyUserId(user.id);
    setError(null);
    try {
      // Re-check server-side: a stale candidate row (member list truncated,
      // or state raced) must not blind-upsert — that would silently demote
      // an existing org admin and wipe their dimension assignments.
      const existing = await getMembershipForUser(user.id, selectedOrgId);
      if (existing !== null) {
        setError(`${user.username} is already a member of this organization.`);
        setCandidates((prev) => prev.filter((c) => c.id !== user.id));
        await loadMembers();
        return;
      }
      await upsertMembership(
        {
          profile_id: user.id,
          is_org_admin: false,
          region_ids: [],
          department_ids: [],
          role_ids: [],
          group_ids: [],
        },
        selectedOrgId,
      );
      setCandidates((prev) => prev.filter((c) => c.id !== user.id));
      await loadMembers();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Could not add member"));
    } finally {
      setBusyUserId(null);
    }
  };

  const withMembership = async (
    userId: string,
    fallbackError: string,
    action: (membership: NonNullable<Awaited<ReturnType<typeof getMembershipForUser>>>) => Promise<void>,
  ) => {
    if (!selectedOrgId) return;
    setBusyUserId(userId);
    setError(null);
    try {
      const membership = await getMembershipForUser(userId, selectedOrgId);
      if (membership === null) {
        setError("Membership no longer exists — refreshing.");
        await loadMembers();
        return;
      }
      await action(membership);
      await loadMembers();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, fallbackError));
    } finally {
      setBusyUserId(null);
    }
  };

  const toggleOrgAdmin = (userId: string) =>
    withMembership(userId, "Could not update membership", async (membership) => {
      await updateMembership(membership.id, { is_org_admin: !membership.is_org_admin }, selectedOrgId ?? undefined);
    });

  const removeMember = (userId: string) =>
    withMembership(userId, "Could not remove member", async (membership) => {
      await deleteMembership(membership.id, selectedOrgId ?? undefined);
    });

  if (isLoading) {
    return <Skeleton className="h-64 w-full" />;
  }

  if (orgs.length === 0) {
    return (
      <Card>
        <CardContent className="pt-6">
          <p className="text-sm text-muted-foreground">
            No organizations yet — create one in the Organizations tab first.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="space-y-6 pt-6">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-lg font-semibold">Memberships</h2>
          <div className="flex flex-wrap gap-2">
            {orgs.map((org) => (
              <button
                key={org.id}
                type="button"
                onClick={() => setSelectedOrgId(org.id)}
                className={cn(
                  "rounded-md border px-2 py-1 text-xs transition-colors",
                  org.id === selectedOrgId
                    ? "border-primary bg-primary text-primary-foreground"
                    : "hover:bg-accent",
                )}
              >
                {org.name}
              </button>
            ))}
          </div>
        </div>

        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        <div className="grid gap-6 md:grid-cols-2">
          <div className="space-y-2">
            <h3 className="text-sm font-semibold">Members</h3>
            {isMembersLoading ? (
              <Skeleton className="h-24 w-full" />
            ) : members.length > 0 ? (
              <ul className="divide-y rounded-md border">
                {members.map((member) => (
                  <li key={member.id} className="flex flex-wrap items-center gap-2 px-3 py-2">
                    <div className="flex min-w-40 flex-1 flex-col">
                      <span className="flex items-center gap-2 text-sm">
                        {member.username}
                        {adminIds.has(member.id) ? <Badge variant="outline">org admin</Badge> : null}
                      </span>
                      <span className="text-xs text-muted-foreground">{member.email}</span>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busyUserId === member.id}
                      onClick={() => void toggleOrgAdmin(member.id)}
                    >
                      {adminIds.has(member.id) ? "Revoke org admin" : "Make org admin"}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      disabled={busyUserId === member.id}
                      onClick={() => void removeMember(member.id)}
                      aria-label={`Remove ${member.username}`}
                    >
                      <UserMinus className="h-4 w-4" />
                    </Button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">No members in this organization.</p>
            )}
            <p className="text-xs text-muted-foreground">
              Dimension assignments (regions, roles, …) are managed per-org in the Admin → Members
              tab.
            </p>
          </div>

          <div className="space-y-2">
            <h3 className="text-sm font-semibold">Add a member</h3>
            <form onSubmit={searchCandidates} className="flex gap-2">
              <Input
                value={candidateQuery}
                onChange={(e) => setCandidateQuery(e.target.value)}
                placeholder="Search users…"
              />
              <Button type="submit" variant="outline" disabled={isSearching}>
                {isSearching ? "Searching…" : "Search"}
              </Button>
            </form>
            {candidates.length > 0 ? (
              <ul className="divide-y rounded-md border">
                {candidates.map((candidate) => (
                  <li key={candidate.id} className="flex items-center gap-2 px-3 py-2">
                    <div className="flex flex-1 flex-col">
                      <span className="text-sm">{candidate.username}</span>
                      <span className="text-xs text-muted-foreground">{candidate.email}</span>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busyUserId === candidate.id}
                      onClick={() => void addMember(candidate)}
                    >
                      <UserPlus className="h-4 w-4" />
                      Add
                    </Button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">
                Search finds active users who aren&apos;t members yet.
              </p>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
