"""Unit tests for can_run (who may manually run a workflow)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from api.auth.dependencies import CurrentUser, OrgContext
from api.services.workflow.permissions import can_run


def _ctx(
    *,
    is_org_admin: bool,
    role_ids: list[uuid.UUID] | None = None,
    group_ids: list[uuid.UUID] | None = None,
) -> OrgContext:
    membership = SimpleNamespace(
        roles=[SimpleNamespace(id=r) for r in (role_ids or [])],
        groups=[SimpleNamespace(id=g) for g in (group_ids or [])],
    )
    user = CurrentUser(sub="s", username="u", email="e@x.com", profile_id=uuid.uuid4(), is_site_admin=False)
    return OrgContext(user=user, org_id=uuid.uuid4(), membership=membership, is_org_admin=is_org_admin)  # type: ignore[arg-type]


def test_org_admin_always_allowed() -> None:
    assert can_run(_ctx(is_org_admin=True), {"mode": "org_admin"}) is True
    assert can_run(_ctx(is_org_admin=True), {"mode": "roles", "role_ids": []}) is True


def test_org_admin_mode_blocks_non_admin() -> None:
    assert can_run(_ctx(is_org_admin=False), {"mode": "org_admin"}) is False
    assert can_run(_ctx(is_org_admin=False), None) is False  # default = org_admin


def test_any_member_mode_allows_non_admin() -> None:
    assert can_run(_ctx(is_org_admin=False), {"mode": "any_member"}) is True


def test_roles_mode_matches_membership() -> None:
    role = uuid.uuid4()
    other = uuid.uuid4()
    perm = {"mode": "roles", "role_ids": [str(role)]}
    assert can_run(_ctx(is_org_admin=False, role_ids=[role]), perm) is True
    assert can_run(_ctx(is_org_admin=False, role_ids=[other]), perm) is False


def test_groups_mode_matches_membership() -> None:
    group = uuid.uuid4()
    perm = {"mode": "roles", "group_ids": [str(group)]}
    assert can_run(_ctx(is_org_admin=False, group_ids=[group]), perm) is True
    assert can_run(_ctx(is_org_admin=False), perm) is False
