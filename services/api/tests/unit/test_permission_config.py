"""Tests for the permission config → masks conversion."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from access_mask import MAX_DEPT, MAX_GROUP, MAX_REGION, MAX_ROLE, decode, encode, expand_member_masks
from api.services.permission_config import calculate_user_masks_from_membership, member_base_masks


@dataclass
class _FakeDim:
    permission_number: int


@dataclass
class _FakeMembership:
    regions: list[_FakeDim]
    departments: list[_FakeDim]
    roles: list[_FakeDim]
    groups: list[_FakeDim]


class TestMemberBaseMasks:
    """The exact region × dept × role × group tuples a membership asserts."""

    def test_no_memberships_yields_single_zero_mask(self) -> None:
        membership = _FakeMembership(regions=[], departments=[], roles=[], groups=[])
        masks = member_base_masks(membership, org_number=1)
        # A user with no dimensions still gets one mask (all zeros in scoped fields)
        assert len(masks) == 1
        d = decode(masks[0])
        assert d.org == 1
        assert (d.region, d.role, d.group, d.dept) == (0, 0, 0, 0)

    def test_single_dimension_values(self) -> None:
        membership = _FakeMembership(
            regions=[_FakeDim(2)],
            departments=[_FakeDim(3)],
            roles=[_FakeDim(4)],
            groups=[_FakeDim(5)],
        )
        masks = member_base_masks(membership, org_number=1)
        assert masks == [encode(org=1, region=2, dept=3, role=4, group=5)]

    def test_cartesian_product(self) -> None:
        """2 regions × 2 roles → 4 distinct masks."""
        membership = _FakeMembership(
            regions=[_FakeDim(1), _FakeDim(2)],
            departments=[],
            roles=[_FakeDim(3), _FakeDim(4)],
            groups=[],
        )
        masks = member_base_masks(membership, org_number=5)
        assert len(masks) == 4

        decoded_pairs = {(decode(m).region, decode(m).role) for m in masks}
        assert decoded_pairs == {(1, 3), (1, 4), (2, 3), (2, 4)}

    def test_all_masks_share_org(self) -> None:
        membership = _FakeMembership(
            regions=[_FakeDim(1), _FakeDim(2)],
            departments=[_FakeDim(3)],
            roles=[_FakeDim(4)],
            groups=[_FakeDim(5), _FakeDim(6)],
        )
        masks = member_base_masks(membership, org_number=42)
        assert all(decode(m).org == 42 for m in masks)

    @pytest.mark.parametrize(
        "n_regions,n_depts,n_roles,n_groups,expected",
        [
            (1, 1, 1, 1, 1),
            (2, 1, 1, 1, 2),
            (2, 2, 1, 1, 4),
            (3, 2, 2, 2, 24),
        ],
    )
    def test_cartesian_counts(self, n_regions: int, n_depts: int, n_roles: int, n_groups: int, expected: int) -> None:
        membership = _FakeMembership(
            regions=[_FakeDim(i) for i in range(1, n_regions + 1)],
            departments=[_FakeDim(i) for i in range(1, n_depts + 1)],
            roles=[_FakeDim(i) for i in range(1, n_roles + 1)],
            groups=[_FakeDim(i) for i in range(1, n_groups + 1)],
        )
        masks = member_base_masks(membership, org_number=1)
        assert len(masks) == expected


class TestCalculateUserMasksExpandsWildcards:
    """What every resolver uses: the base tuples expanded with wildcard variants,
    so a folder that leaves a dimension open (MAX) matches by plain equality."""

    def test_is_the_expansion_of_the_base_masks(self) -> None:
        membership = _FakeMembership(
            regions=[_FakeDim(2), _FakeDim(6)], departments=[_FakeDim(3)], roles=[_FakeDim(4)], groups=[_FakeDim(5)]
        )
        base = member_base_masks(membership, org_number=1)
        built = calculate_user_masks_from_membership(membership, org_number=1)
        assert set(built) == set(expand_member_masks(base))
        assert built[0] == base[0]

    def test_department_only_folder_mask_is_included(self) -> None:
        membership = _FakeMembership(regions=[_FakeDim(2)], departments=[_FakeDim(3)], roles=[], groups=[])
        masks = calculate_user_masks_from_membership(membership, org_number=1)
        assert encode(org=1, region=MAX_REGION, dept=3, role=MAX_ROLE, group=MAX_GROUP) in masks
        # ...but not another department's.
        assert encode(org=1, region=MAX_REGION, dept=4, role=MAX_ROLE, group=MAX_GROUP) not in masks

    def test_org_is_never_a_wildcard(self) -> None:
        membership = _FakeMembership(regions=[_FakeDim(2)], departments=[_FakeDim(3)], roles=[], groups=[])
        assert {decode(m).org for m in calculate_user_masks_from_membership(membership, org_number=7)} == {7}

    def test_fully_open_mask_is_included(self) -> None:
        membership = _FakeMembership(regions=[], departments=[], roles=[], groups=[])
        masks = calculate_user_masks_from_membership(membership, org_number=1)
        assert encode(org=1, region=MAX_REGION, dept=MAX_DEPT, role=MAX_ROLE, group=MAX_GROUP) in masks
        assert len(masks) == 16


class TestMaskCap:
    """A membership whose expanded masks exceed MAX_ACCESS_KEYS fails closed with a
    clear error instead of producing a filter brain-api would reject (or worse,
    truncate)."""

    def test_size_is_product_of_counts_plus_one(self) -> None:
        membership = _FakeMembership(
            regions=[_FakeDim(1), _FakeDim(2)], departments=[_FakeDim(3)], roles=[], groups=[_FakeDim(4), _FakeDim(5)]
        )
        # (2+1) regions × (1+1) depts × (0→{0}+1) roles × (2+1) groups
        assert len(calculate_user_masks_from_membership(membership, org_number=1)) == 3 * 2 * 2 * 3

    def test_over_the_cap_raises(self) -> None:
        from api.services.permission_config import TooManyAccessMasks

        membership = _FakeMembership(
            regions=[_FakeDim(i) for i in range(1, 11)],
            departments=[_FakeDim(i) for i in range(1, 11)],
            roles=[_FakeDim(i) for i in range(1, 11)],
            groups=[_FakeDim(i) for i in range(1, 11)],
        )  # 11^4 = 14641 > 8192
        with pytest.raises(TooManyAccessMasks):
            calculate_user_masks_from_membership(membership, org_number=1)

    async def test_api_maps_it_to_422(self) -> None:
        from api.exception_handlers import make_too_many_masks_handler
        from api.services.permission_config import TooManyAccessMasks

        handler = make_too_many_masks_handler(["http://localhost:3002"])
        resp = await handler(MagicMock(headers={}), TooManyAccessMasks(14641))
        assert resp.status_code == 422
        assert b"8192" in resp.body


class TestMasksFromAssignments:
    """``calculate_masks_from_assignments`` is the single mask builder for people and
    for API keys bound directly to dimension assignments. A key holding exactly a
    member's assignments must read with exactly that member's masks."""

    def test_roles_only_matches_a_member_with_only_those_roles(self) -> None:
        from api.services.permission_config import calculate_masks_from_assignments

        member = _FakeMembership(regions=[], departments=[], roles=[_FakeDim(4), _FakeDim(7)], groups=[])
        assert calculate_masks_from_assignments(
            1, regions=[], departments=[], roles=[4, 7], groups=[]
        ) == calculate_user_masks_from_membership(member, org_number=1)

    def test_every_dimension_matches_the_membership(self) -> None:
        from api.services.permission_config import calculate_masks_from_assignments

        member = _FakeMembership(
            regions=[_FakeDim(2)], departments=[_FakeDim(3), _FakeDim(9)], roles=[_FakeDim(4)], groups=[_FakeDim(5)]
        )
        assert calculate_masks_from_assignments(
            11, regions=[2], departments=[3, 9], roles=[4], groups=[5]
        ) == calculate_user_masks_from_membership(member, org_number=11)

    def test_no_assignments_is_a_member_with_none(self) -> None:
        """Empty means "unassigned" (dimension 0), never "every value"."""
        from api.services.permission_config import calculate_masks_from_assignments

        masks = calculate_masks_from_assignments(1, regions=[], departments=[], roles=[], groups=[])
        assert sorted(masks) == sorted(expand_member_masks([encode(org=1, region=0, dept=0, role=0, group=0)]))
        assert masks  # never empty

    def test_over_the_cap_raises(self) -> None:
        from api.services.permission_config import TooManyAccessMasks, calculate_masks_from_assignments

        many = list(range(1, 11))
        with pytest.raises(TooManyAccessMasks):
            calculate_masks_from_assignments(1, regions=many, departments=many, roles=many, groups=many)
