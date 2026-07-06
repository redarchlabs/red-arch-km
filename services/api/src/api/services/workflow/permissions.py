"""Who may manually run a workflow.

Org admins may always run. Beyond that, a workflow's ``run_permission`` (JSONB on
the row) widens it to any member, or to members holding specific roles/groups —
checked against the caller's membership (roles/groups are eager-loaded by
``require_org_access``).
"""

from __future__ import annotations

from typing import Any

from api.auth.dependencies import OrgContext


def can_run(ctx: OrgContext, run_permission: dict[str, Any] | None) -> bool:
    if ctx.is_org_admin:
        return True
    perm = run_permission or {}
    mode = perm.get("mode", "org_admin")
    if mode == "any_member":
        return True
    if mode == "roles":
        allowed_roles = {str(r) for r in (perm.get("role_ids") or [])}
        allowed_groups = {str(g) for g in (perm.get("group_ids") or [])}
        my_roles = {str(r.id) for r in getattr(ctx.membership, "roles", []) or []}
        my_groups = {str(g.id) for g in getattr(ctx.membership, "groups", []) or []}
        return bool(allowed_roles & my_roles) or bool(allowed_groups & my_groups)
    # "org_admin" (or unknown) → only admins, already handled above.
    return False
