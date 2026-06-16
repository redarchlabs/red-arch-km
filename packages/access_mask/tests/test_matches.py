"""Tests for AccessMask permission matching logic."""

import pytest
from access_mask import (
    MAX_DEPT,
    MAX_GROUP,
    MAX_REGION,
    MAX_ROLE,
    AccessMask,
    encode,
    matches,
)


class TestMatches:
    def test_exact_match(self) -> None:
        user = encode(org=1, region=2, role=4, dept=3)
        doc = encode(org=1, region=2, role=4, dept=3)
        assert matches(user, doc) is True

    def test_org_mismatch_denies(self) -> None:
        user = encode(org=1, region=2, role=4, dept=3)
        doc = encode(org=2, region=2, role=4, dept=3)
        assert matches(user, doc) is False

    def test_role_wildcard_allows(self) -> None:
        user = encode(org=1, region=2, role=4, dept=3)
        doc = encode(org=1, region=2, role=MAX_ROLE, dept=3)
        assert matches(user, doc) is True

    def test_all_wildcards_allows(self) -> None:
        """Document with all wildcards (except org) allows any user in that org."""
        user = encode(org=1, region=10, role=5, group=50, dept=3)
        doc = encode(
            org=1,
            region=MAX_REGION,
            role=MAX_ROLE,
            group=MAX_GROUP,
            dept=MAX_DEPT,
        )
        assert matches(user, doc) is True

    def test_region_mismatch_denies(self) -> None:
        user = encode(org=1, region=2, role=4, dept=3)
        doc = encode(org=1, region=3, role=4, dept=3)
        assert matches(user, doc) is False

    def test_dept_mismatch_denies(self) -> None:
        user = encode(org=1, region=2, role=4, dept=3)
        doc = encode(org=1, region=2, role=4, dept=5)
        assert matches(user, doc) is False

    def test_group_mismatch_denies(self) -> None:
        user = encode(org=1, region=2, role=4, group=10, dept=3)
        doc = encode(org=1, region=2, role=4, group=20, dept=3)
        assert matches(user, doc) is False

    def test_group_wildcard_allows(self) -> None:
        user = encode(org=1, group=10)
        doc = encode(org=1, group=MAX_GROUP)
        assert matches(user, doc) is True

    def test_partial_wildcards(self) -> None:
        """Wildcard on role, but specific region must still match."""
        user = encode(org=1, region=5, role=3)
        doc = encode(org=1, region=5, role=MAX_ROLE)
        assert matches(user, doc) is True

        doc_wrong_region = encode(org=1, region=6, role=MAX_ROLE)
        assert matches(user, doc_wrong_region) is False

    def test_zero_masks(self) -> None:
        assert matches(0, 0) is True

    def test_user_has_wildcard_doc_specific(self) -> None:
        """User having wildcard values does NOT grant access to specific doc values."""
        user = encode(org=1, region=MAX_REGION)
        doc = encode(org=1, region=5)
        # Wildcard on document means "any user", but wildcard on user doesn't mean "any doc"
        assert matches(user, doc) is False


class TestAccessMaskClass:
    def test_frozen_dataclass(self) -> None:
        mask = AccessMask(value=encode(org=1, region=2))
        with pytest.raises(AttributeError):
            mask.value = 0  # type: ignore[misc]

    def test_decoded_property(self) -> None:
        mask = AccessMask(value=encode(org=1, region=2, role=3))
        d = mask.decoded
        assert d.org == 1
        assert d.region == 2
        assert d.role == 3

    def test_matches_method(self) -> None:
        user = AccessMask(value=encode(org=1, region=2))
        doc = AccessMask(value=encode(org=1, region=2))
        assert user.matches(doc) is True

    def test_matches_method_denies(self) -> None:
        user = AccessMask(value=encode(org=1, region=2))
        doc = AccessMask(value=encode(org=2, region=2))
        assert user.matches(doc) is False


class TestMultiMaskScenarios:
    """Test scenarios where a user has multiple masks (Cartesian product of memberships)."""

    def test_any_mask_grants_access(self) -> None:
        """If ANY of a user's masks matches, access is granted."""
        user_masks = [
            encode(org=1, region=1, role=1, dept=1),
            encode(org=1, region=2, role=3, dept=1),
            encode(org=1, region=1, role=5, dept=2),
        ]
        doc = encode(org=1, region=2, role=3, dept=1)

        has_access = any(matches(um, doc) for um in user_masks)
        assert has_access is True

    def test_no_mask_grants_access(self) -> None:
        user_masks = [
            encode(org=1, region=1, role=1, dept=1),
            encode(org=1, region=2, role=3, dept=1),
        ]
        doc = encode(org=1, region=5, role=10, dept=8)

        has_access = any(matches(um, doc) for um in user_masks)
        assert has_access is False

    @pytest.mark.parametrize(
        "doc_region,doc_role,expected",
        [
            (2, 3, True),  # Exact match with second user mask
            (MAX_REGION, 3, True),  # Region wildcard
            (2, MAX_ROLE, True),  # Role wildcard
            (5, 10, False),  # No match
        ],
    )
    def test_parametrized_access_check(self, doc_region: int, doc_role: int, expected: bool) -> None:
        user_masks = [
            encode(org=1, region=1, role=1),
            encode(org=1, region=2, role=3),
        ]
        doc = encode(org=1, region=doc_region, role=doc_role)
        assert any(matches(um, doc) for um in user_masks) is expected
