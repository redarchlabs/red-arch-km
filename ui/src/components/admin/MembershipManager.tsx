"use client";

import { Save } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type Dimension,
  type DimensionKind,
  listDimensions,
} from "@/lib/api/dimensions";
import {
  getMembershipForUser,
  listUsersInOrg,
  upsertMembership,
} from "@/lib/api/memberships";
import { cn } from "@/lib/utils";

interface UserRow {
  id: string;
  username: string;
  email: string;
}

interface Selections {
  is_org_admin: boolean;
  regions: Set<string>;
  departments: Set<string>;
  roles: Set<string>;
  groups: Set<string>;
}

const EMPTY_SELECTIONS: Selections = {
  is_org_admin: false,
  regions: new Set(),
  departments: new Set(),
  roles: new Set(),
  groups: new Set(),
};

const DIMENSION_KINDS: Array<{ kind: DimensionKind; key: keyof Omit<Selections, "is_org_admin"> }> = [
  { kind: "regions", key: "regions" },
  { kind: "departments", key: "departments" },
  { kind: "roles", key: "roles" },
  { kind: "groups", key: "groups" },
];

export function MembershipManager() {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [activeUserId, setActiveUserId] = useState<string | null>(null);
  const [options, setOptions] = useState<Record<DimensionKind, Dimension[]>>({
    regions: [], departments: [], roles: [], groups: [],
  });
  const [selections, setSelections] = useState<Selections>(EMPTY_SELECTIONS);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadInitial = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [userRows, regions, departments, roles, groups] = await Promise.all([
        listUsersInOrg(),
        listDimensions("regions"),
        listDimensions("departments"),
        listDimensions("roles"),
        listDimensions("groups"),
      ]);
      setUsers(userRows);
      setOptions({ regions, departments, roles, groups });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load data");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadInitial();
  }, [loadInitial]);

  const loadMembership = useCallback(async (userId: string) => {
    setError(null);
    setNotice(null);
    try {
      const membership = await getMembershipForUser(userId);
      setSelections(
        membership === null
          ? { ...EMPTY_SELECTIONS }
          : {
              is_org_admin: membership.is_org_admin,
              regions: new Set(membership.regions.map((r) => r.id)),
              departments: new Set(membership.departments.map((d) => d.id)),
              roles: new Set(membership.roles.map((r) => r.id)),
              groups: new Set(membership.groups.map((g) => g.id)),
            },
      );
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load membership");
    }
  }, []);

  const handleSelectUser = async (userId: string) => {
    setActiveUserId(userId);
    await loadMembership(userId);
  };

  const toggle = (key: keyof Omit<Selections, "is_org_admin">, id: string) => {
    setSelections((prev) => {
      const next = new Set(prev[key]);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { ...prev, [key]: next };
    });
  };

  const handleSave = async () => {
    if (!activeUserId) return;
    setIsSaving(true);
    setError(null);
    setNotice(null);
    try {
      await upsertMembership({
        profile_id: activeUserId,
        is_org_admin: selections.is_org_admin,
        region_ids: [...selections.regions],
        department_ids: [...selections.departments],
        role_ids: [...selections.roles],
        group_ids: [...selections.groups],
      });
      setNotice("Membership saved");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setIsSaving(false);
    }
  };

  const activeUser = useMemo(
    () => users.find((u) => u.id === activeUserId) ?? null,
    [users, activeUserId],
  );

  if (isLoading) {
    return <Skeleton className="h-64 w-full" />;
  }

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="grid grid-cols-[minmax(240px,1fr)_2fr] gap-6">
          <div>
            <h2 className="mb-2 text-lg font-semibold">Members</h2>
            {users.length === 0 ? (
              <p className="text-sm text-muted-foreground">No members yet.</p>
            ) : (
              <ul className="divide-y rounded-md border">
                {users.map((user) => (
                  <li key={user.id}>
                    <button
                      type="button"
                      onClick={() => void handleSelectUser(user.id)}
                      className={cn(
                        "flex w-full flex-col items-start px-3 py-2 text-left text-sm hover:bg-accent",
                        user.id === activeUserId && "bg-accent font-medium",
                      )}
                    >
                      <span>{user.username}</span>
                      <span className="text-xs text-muted-foreground">{user.email}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div>
            {activeUser === null ? (
              <p className="text-sm text-muted-foreground">Select a member to edit.</p>
            ) : (
              <div className="space-y-4">
                <div>
                  <h3 className="text-base font-semibold">{activeUser.username}</h3>
                  <p className="text-sm text-muted-foreground">{activeUser.email}</p>
                </div>

                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={selections.is_org_admin}
                    onChange={(e) =>
                      setSelections((prev) => ({ ...prev, is_org_admin: e.target.checked }))
                    }
                    disabled={isSaving}
                  />
                  Organization admin
                  {selections.is_org_admin ? (
                    <Badge variant="default">admin</Badge>
                  ) : null}
                </label>

                {DIMENSION_KINDS.map(({ kind, key }) => (
                  <div key={kind}>
                    <p className="mb-1.5 text-sm font-medium capitalize">{kind}</p>
                    {options[kind].length === 0 ? (
                      <p className="text-xs text-muted-foreground">
                        No {kind} defined yet.
                      </p>
                    ) : (
                      <div className="flex flex-wrap gap-2">
                        {options[kind].map((opt) => {
                          const active = selections[key].has(opt.id);
                          return (
                            <button
                              key={opt.id}
                              type="button"
                              onClick={() => toggle(key, opt.id)}
                              disabled={isSaving}
                              className={cn(
                                "rounded-md border px-2 py-1 text-xs transition-colors",
                                active
                                  ? "border-primary bg-primary text-primary-foreground"
                                  : "hover:bg-accent",
                              )}
                            >
                              {opt.name}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                ))}

                {error ? <p className="text-sm text-destructive">{error}</p> : null}
                {notice ? <p className="text-sm text-green-600">{notice}</p> : null}

                <Button onClick={() => void handleSave()} disabled={isSaving}>
                  <Save className="h-4 w-4" />
                  {isSaving ? "Saving…" : "Save membership"}
                </Button>
              </div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
