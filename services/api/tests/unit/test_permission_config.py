"""Tests for the permission config → masks conversion."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from access_mask import MAX_DEPT, MAX_GROUP, MAX_REGION, MAX_ROLE, decode, encode

from api.services.permission_config import calculate_user_masks_from_membership


@dataclass
class _FakeDim:
    permission_number: int


@dataclass
class _FakeMembership:
    regions: list[_FakeDim]
    departments: list[_FakeDim]
    roles: list[_FakeDim]
    groups: list[_FakeDim]


class TestCalculateUserMasksFromMembership:
    def test_no_memberships_yields_single_zero_mask(self) -> None:
        membership = _FakeMembership(regions=[], departments=[], roles=[], groups=[])
        masks = calculate_user_masks_from_membership(membership, org_number=1)
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
        masks = calculate_user_masks_from_membership(membership, org_number=1)
        assert masks == [encode(org=1, region=2, dept=3, role=4, group=5)]

    def test_cartesian_product(self) -> None:
        """2 regions × 2 roles → 4 distinct masks."""
        membership = _FakeMembership(
            regions=[_FakeDim(1), _FakeDim(2)],
            departments=[],
            roles=[_FakeDim(3), _FakeDim(4)],
            groups=[],
        )
        masks = calculate_user_masks_from_membership(membership, org_number=5)
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
        masks = calculate_user_masks_from_membership(membership, org_number=42)
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
    def test_cartesian_counts(
        self, n_regions: int, n_depts: int, n_roles: int, n_groups: int, expected: int
    ) -> None:
        membership = _FakeMembership(
            regions=[_FakeDim(i) for i in range(1, n_regions + 1)],
            departments=[_FakeDim(i) for i in range(1, n_depts + 1)],
            roles=[_FakeDim(i) for i in range(1, n_roles + 1)],
            groups=[_FakeDim(i) for i in range(1, n_groups + 1)],
        )
        masks = calculate_user_masks_from_membership(membership, org_number=1)
        assert len(masks) == expected
