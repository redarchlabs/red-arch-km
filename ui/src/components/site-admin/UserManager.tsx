"use client";

import { Search } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  listAllUsers,
  updateAdminUser,
  type AdminUser,
  type AdminUserUpdateInput,
} from "@/lib/api/adminUsers";
import { getApiErrorMessage } from "@/lib/api/errors";
import { fetchMe } from "@/lib/api/users";

const PAGE_SIZE = 50;

export function UserManager() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(0);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [myId, setMyId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Drop out-of-order responses from rapid page/search changes.
  const loadSeqRef = useRef(0);

  const load = useCallback(async () => {
    const seq = ++loadSeqRef.current;
    setIsLoading(true);
    setError(null);
    try {
      const result = await listAllUsers({ page, pageSize: PAGE_SIZE, q: search || undefined });
      if (seq !== loadSeqRef.current) return;
      setUsers(result.items);
      setTotal(result.total);
      setPages(result.pages);
    } catch (e: unknown) {
      if (seq === loadSeqRef.current) {
        setError(getApiErrorMessage(e, "Failed to load users"));
      }
    } finally {
      if (seq === loadSeqRef.current) {
        setIsLoading(false);
      }
    }
  }, [page, search]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    // Own row's destructive actions are disabled — the backend rejects them
    // anyway (400), but don't offer a button that can only fail.
    void fetchMe().then((me) => setMyId(me.id)).catch(() => undefined);
  }, []);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    setSearch(query.trim());
  };

  const applyUpdate = async (user: AdminUser, input: AdminUserUpdateInput) => {
    setBusyId(user.id);
    setError(null);
    try {
      await updateAdminUser(user.id, input);
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Update failed"));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Card>
      <CardContent className="space-y-4 pt-6">
        <div>
          <h2 className="text-lg font-semibold">Users</h2>
          <p className="text-sm text-muted-foreground">
            {total} users · promote site admins or deactivate accounts.
          </p>
        </div>

        <form onSubmit={handleSearch} className="flex gap-2">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by username or email…"
            className="max-w-sm"
          />
          <Button type="submit" variant="outline">
            <Search className="h-4 w-4" />
            Search
          </Button>
        </form>

        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        {isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : users.length > 0 ? (
          <ul className="divide-y rounded-md border">
            {users.map((user) => {
              const isSelf = user.id === myId;
              const isBusy = busyId === user.id;
              return (
                <li key={user.id} className="flex flex-wrap items-center gap-2 px-3 py-2">
                  <div className="flex min-w-48 flex-1 flex-col">
                    <span className="flex items-center gap-2 text-sm font-medium">
                      {user.username}
                      {isSelf ? <Badge variant="outline">you</Badge> : null}
                      {user.is_site_admin ? <Badge>site admin</Badge> : null}
                      {!user.is_active ? <Badge variant="destructive">deactivated</Badge> : null}
                    </span>
                    <span className="text-xs text-muted-foreground">{user.email}</span>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={isBusy || (isSelf && user.is_site_admin)}
                      onClick={() => void applyUpdate(user, { is_site_admin: !user.is_site_admin })}
                    >
                      {user.is_site_admin ? "Demote" : "Make site admin"}
                    </Button>
                    <Button
                      variant={user.is_active ? "destructive" : "outline"}
                      size="sm"
                      disabled={isBusy || (isSelf && user.is_active)}
                      onClick={() => void applyUpdate(user, { is_active: !user.is_active })}
                    >
                      {user.is_active ? "Deactivate" : "Reactivate"}
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No users match.</p>
        )}

        {pages > 1 ? (
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              Previous
            </Button>
            <span className="text-sm text-muted-foreground">
              Page {page} of {pages}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= pages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
