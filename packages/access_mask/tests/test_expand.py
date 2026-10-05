"""Tests for wildcard expansion of member masks.

Folder/document masks carry a dimension's MAX value as a wildcard ("any value").
Every store compares masks by exact integer equality (Postgres array overlap,
Qdrant MatchAny, Neo4j ``k IN $keys``), so a member's masks are expanded with each
combination of their own value / the wildcard per dimension. Equality against the
expanded set must then agree exactly with :func:`matches`.
"""

from itertools import product

from access_mask import (
    MAX_DEPT,
    MAX_GROUP,
    MAX_REGION,
    MAX_ROLE,
    decode,
    encode,
    expand_member_masks,
    expand_wildcards,
    matches,
)


class TestExpandWildcards:
    def test_concrete_mask_has_sixteen_variants(self) -> None:
        variants = expand_wildcards(encode(org=9, region=3, role=2, group=7, dept=5))
        assert len(variants) == 16
        assert len(set(variants)) == 16

    def test_original_mask_comes_first(self) -> None:
        mask = encode(org=9, region=3, role=2, group=7, dept=5)
        assert expand_wildcards(mask)[0] == mask

    def test_org_is_never_wildcarded(self) -> None:
        for v in expand_wildcards(encode(org=9, region=3, role=2, group=7, dept=5)):
            assert decode(v).org == 9

    def test_every_dimension_is_either_own_value_or_wildcard(self) -> None:
        for v in expand_wildcards(encode(org=9, region=3, role=2, group=7, dept=5)):
            d = decode(v)
            assert d.region in (3, MAX_REGION)
            assert d.role in (2, MAX_ROLE)
            assert d.group in (7, MAX_GROUP)
            assert d.dept in (5, MAX_DEPT)

    def test_fully_wildcarded_variant_is_present(self) -> None:
        variants = expand_wildcards(encode(org=9, region=3, role=2, group=7, dept=5))
        assert encode(org=9, region=MAX_REGION, role=MAX_ROLE, group=MAX_GROUP, dept=MAX_DEPT) in variants

    def test_a_value_already_at_max_does_not_duplicate(self) -> None:
        variants = expand_wildcards(encode(org=9, region=MAX_REGION, role=2, group=7, dept=5))
        assert len(variants) == 8
        assert len(set(variants)) == 8


class TestExpandMemberMasks:
    def test_dedupes_and_keeps_order(self) -> None:
        a = encode(org=9, region=3, dept=5)
        b = encode(org=9, region=4, dept=5)
        out = expand_member_masks([a, b, a])
        assert out[0] == a
        assert len(out) == len(set(out))
        assert set(expand_wildcards(a)) | set(expand_wildcards(b)) == set(out)

    def test_empty_in_empty_out(self) -> None:
        assert expand_member_masks([]) == []


class TestEqualityAgreesWithMatches:
    """The point of the expansion: set membership == ``matches()``, everywhere."""

    def test_exhaustive_over_a_small_grid(self) -> None:
        regions = (0, 3, 4, MAX_REGION)
        roles = (0, 2, MAX_ROLE)
        groups = (0, 7, MAX_GROUP)
        depts = (0, 5, 6, MAX_DEPT)
        users = [
            encode(org=9, region=r, role=ro, group=g, dept=d) for r, ro, g, d in product((0, 3), (0, 2), (0, 7), (5, 6))
        ]
        docs = [
            encode(org=org, region=r, role=ro, group=g, dept=d)
            for org, r, ro, g, d in product((9, 10), regions, roles, groups, depts)
        ]
        for user in users:
            expanded = set(expand_wildcards(user))
            for doc in docs:
                assert (doc in expanded) == matches(user, doc), (decode(user), decode(doc))

    def test_department_only_folder_admits_that_department(self) -> None:
        member = encode(org=9, region=3, role=0, group=0, dept=5)
        folder = encode(org=9, region=MAX_REGION, role=MAX_ROLE, group=MAX_GROUP, dept=5)
        assert folder in expand_wildcards(member)

    def test_department_only_folder_rejects_other_departments(self) -> None:
        member = encode(org=9, region=3, role=0, group=0, dept=6)
        folder = encode(org=9, region=MAX_REGION, role=MAX_ROLE, group=MAX_GROUP, dept=5)
        assert folder not in expand_wildcards(member)

    def test_fully_wildcarded_folder_of_another_org_never_matches(self) -> None:
        member = encode(org=9, region=3, dept=5)
        other_org = encode(org=10, region=MAX_REGION, role=MAX_ROLE, group=MAX_GROUP, dept=MAX_DEPT)
        assert other_org not in expand_wildcards(member)


class TestMemberMasks:
    """The expanded set built per dimension: product of (own values ∪ {MAX}).

    Same set as expanding every base tuple ×16, at (r+1)(d+1)(ro+1)(g+1) entries
    instead of r·d·ro·g·16 work, and with no duplicates to strip."""

    def _base(self, regions, roles, groups, depts) -> list[int]:
        return [
            encode(org=9, region=r, role=ro, group=g, dept=d) for r, ro, g, d in product(regions, roles, groups, depts)
        ]

    def test_same_set_as_per_tuple_expansion(self) -> None:
        from access_mask import member_masks

        dims = ([3, 4], [2], [7, 8, 9], [5, 6])
        built = member_masks(org=9, regions=dims[0], roles=dims[1], groups=dims[2], depts=dims[3])
        assert set(built) == set(expand_member_masks(self._base(*dims)))
        assert len(built) == len(set(built))

    def test_size_is_product_of_counts_plus_one(self) -> None:
        from access_mask import member_masks

        built = member_masks(org=9, regions=[1, 2], roles=[3], groups=[4, 5, 6], depts=[7])
        assert len(built) == 3 * 2 * 4 * 2

    def test_base_tuples_come_first(self) -> None:
        from access_mask import member_masks

        built = member_masks(org=9, regions=[3], roles=[2], groups=[7], depts=[5])
        assert built[0] == encode(org=9, region=3, role=2, group=7, dept=5)

    def test_org_never_wildcarded(self) -> None:
        from access_mask import member_masks

        assert {decode(m).org for m in member_masks(org=9, regions=[1], roles=[0], groups=[0], depts=[2])} == {9}

    def test_cap_constant(self) -> None:
        from access_mask import MAX_ACCESS_KEYS

        assert MAX_ACCESS_KEYS == 8192
