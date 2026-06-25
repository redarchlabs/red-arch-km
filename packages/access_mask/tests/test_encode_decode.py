"""Tests for AccessMask encode/decode round-trip and edge cases."""

import pytest
from access_mask import (
    MAX_DEPT,
    MAX_GROUP,
    MAX_ORG_ID,
    MAX_REGION,
    MAX_ROLE,
    decode,
    encode,
)


class TestEncode:
    def test_all_zeros(self) -> None:
        assert encode() == 0

    def test_org_only(self) -> None:
        mask = encode(org=1)
        d = decode(mask)
        assert d.org == 1
        assert d.region == 0
        assert d.role == 0
        assert d.group == 0
        assert d.dept == 0

    def test_all_fields(self) -> None:
        mask = encode(org=1, region=2, role=4, group=3, dept=3)
        d = decode(mask)
        assert d.org == 1
        assert d.region == 2
        assert d.role == 4
        assert d.group == 3
        assert d.dept == 3

    def test_max_values(self) -> None:
        mask = encode(
            org=MAX_ORG_ID,
            region=MAX_REGION,
            role=MAX_ROLE,
            group=MAX_GROUP,
            dept=MAX_DEPT,
        )
        d = decode(mask)
        assert d.org == MAX_ORG_ID
        assert d.region == MAX_REGION
        assert d.role == MAX_ROLE
        assert d.group == MAX_GROUP
        assert d.dept == MAX_DEPT

    def test_each_field_isolated(self) -> None:
        """Each field occupies its own bit range with no overlap."""
        masks = [
            encode(org=1),
            encode(region=1),
            encode(role=1),
            encode(group=1),
            encode(dept=1),
        ]
        # All masks should be distinct (no bit overlap)
        assert len(set(masks)) == 5

    def test_group_only(self) -> None:
        mask = encode(group=3)
        d = decode(mask)
        assert d == (0, 0, 0, 3, 0)
        assert mask == 48  # 3 << 4

    def test_broad_document_mask(self) -> None:
        """Document with all wildcards except org."""
        mask = encode(
            org=1,
            region=MAX_REGION,
            role=MAX_ROLE,
            group=MAX_GROUP,
            dept=MAX_DEPT,
        )
        d = decode(mask)
        assert d.org == 1
        assert d.region == MAX_REGION
        assert d.role == MAX_ROLE
        assert d.group == MAX_GROUP
        assert d.dept == MAX_DEPT


class TestEncodeValidation:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("org", -1),
            ("org", MAX_ORG_ID + 1),
            ("region", -1),
            ("region", MAX_REGION + 1),
            ("role", -1),
            ("role", MAX_ROLE + 1),
            ("group", -1),
            ("group", MAX_GROUP + 1),
            ("dept", -1),
            ("dept", MAX_DEPT + 1),
        ],
    )
    def test_out_of_range_raises(self, field: str, value: int) -> None:
        with pytest.raises(ValueError, match="out of range"):
            encode(**{field: value})


class TestDecode:
    def test_zero(self) -> None:
        assert decode(0) == (0, 0, 0, 0, 0)

    def test_known_value(self) -> None:
        d = decode(131072)
        assert d.region == 2

    def test_round_trip(self) -> None:
        original = encode(org=5, region=10, role=3, group=50, dept=7)
        d = decode(original)
        reconstructed = encode(org=d.org, region=d.region, role=d.role, group=d.group, dept=d.dept)
        assert original == reconstructed

    @pytest.mark.parametrize(
        "org,region,role,group,dept",
        [
            (0, 0, 0, 0, 0),
            (1, 1, 1, 1, 1),
            (MAX_ORG_ID, MAX_REGION, MAX_ROLE, MAX_GROUP, MAX_DEPT),
            (100, 15, 20, 64, 8),
            (2047, 0, 0, 0, 0),
            (0, 0, 0, 0, 15),
        ],
    )
    def test_round_trip_parametrized(self, org: int, region: int, role: int, group: int, dept: int) -> None:
        mask = encode(org=org, region=region, role=role, group=group, dept=dept)
        d = decode(mask)
        assert d == (org, region, role, group, dept)
