"""AccessMask: encode, decode, and match 32-bit RBAC permission masks."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from access_mask.constants import (
    DEPT_SHIFT,
    GROUP_SHIFT,
    MAX_DEPT,
    MAX_GROUP,
    MAX_ORG_ID,
    MAX_REGION,
    MAX_ROLE,
    ORG_SHIFT,
    REGION_SHIFT,
    ROLE_SHIFT,
)


class DecodedMask(NamedTuple):
    """Immutable decoded representation of an access mask."""

    org: int
    region: int
    role: int
    group: int
    dept: int


@dataclass(frozen=True, slots=True)
class AccessMask:
    """Wraps a 32-bit integer access mask with decode/match helpers."""

    value: int

    @property
    def decoded(self) -> DecodedMask:
        return decode(self.value)

    def matches(self, document_mask: AccessMask) -> bool:
        return matches(self.value, document_mask.value)


def encode(
    *,
    org: int = 0,
    region: int = 0,
    role: int = 0,
    group: int = 0,
    dept: int = 0,
) -> int:
    """Encode permission components into a 32-bit integer.

    Raises ValueError if any component is out of range.
    """
    _validate_range("org", org, MAX_ORG_ID)
    _validate_range("region", region, MAX_REGION)
    _validate_range("role", role, MAX_ROLE)
    _validate_range("group", group, MAX_GROUP)
    _validate_range("dept", dept, MAX_DEPT)

    return (
        (org << ORG_SHIFT)
        | (region << REGION_SHIFT)
        | (role << ROLE_SHIFT)
        | (group << GROUP_SHIFT)
        | (dept << DEPT_SHIFT)
    )


def decode(mask: int) -> DecodedMask:
    """Decode a 32-bit integer into its permission components."""
    return DecodedMask(
        org=(mask >> ORG_SHIFT) & MAX_ORG_ID,
        region=(mask >> REGION_SHIFT) & MAX_REGION,
        role=(mask >> ROLE_SHIFT) & MAX_ROLE,
        group=(mask >> GROUP_SHIFT) & MAX_GROUP,
        dept=(mask >> DEPT_SHIFT) & MAX_DEPT,
    )


def matches(user_mask: int, doc_mask: int) -> bool:
    """Check if a user mask grants access to a document mask.

    A document field set to its MAX value acts as a wildcard (any user value matches).
    The org field must always match exactly (no wildcard).
    """
    u = decode(user_mask)
    d = decode(doc_mask)

    if u.org != d.org:
        return False

    return (
        _field_matches(u.region, d.region, MAX_REGION)
        and _field_matches(u.role, d.role, MAX_ROLE)
        and _field_matches(u.group, d.group, MAX_GROUP)
        and _field_matches(u.dept, d.dept, MAX_DEPT)
    )


def expand_wildcards(user_mask: int) -> tuple[int, ...]:
    """Every mask a member's mask can equal on the document side.

    Folder and document masks use a dimension's MAX value as a wildcard, and the
    stores that filter on masks (Postgres array overlap, Qdrant ``MatchAny``,
    Neo4j ``k IN $keys``) compare integers for equality — they cannot evaluate
    :func:`matches`. Expanding the member's mask with each combination of "own
    value" / "wildcard" across region, role, group and dept (up to 16 variants)
    makes ``doc_mask in expand_wildcards(user)`` exactly equivalent to
    ``matches(user, doc_mask)``.

    The org is never wildcarded: there is no org wildcard, so no expansion can
    reach another org. The member's own mask comes first; duplicates (a dimension
    whose value already is its MAX) are dropped.
    """
    d = decode(user_mask)
    out: dict[int, None] = {}
    for region in dict.fromkeys((d.region, MAX_REGION)):
        for role in dict.fromkeys((d.role, MAX_ROLE)):
            for group in dict.fromkeys((d.group, MAX_GROUP)):
                for dept in dict.fromkeys((d.dept, MAX_DEPT)):
                    out[encode(org=d.org, region=region, role=role, group=group, dept=dept)] = None
    return tuple(out)


def expand_member_masks(user_masks: Iterable[int]) -> list[int]:
    """:func:`expand_wildcards` over a member's masks, de-duplicated, order stable.

    The reference expansion over an arbitrary mask list. Requesters' masks —
    people, agent actors, and API keys scoped to dimension assignments — are built
    by :func:`member_masks` (via ``calculate_masks_from_assignments``), which
    produces the same set per dimension; tests pin the two equal.
    """
    out: dict[int, None] = {}
    for mask in user_masks:
        for variant in expand_wildcards(mask):
            out[variant] = None
    return list(out)


# Upper bound on a requester's mask list, shared by the API and every brain-api
# request that takes access_keys. member_masks yields
# (regions+1) x (depts+1) x (roles+1) x (groups+1) masks.
MAX_ACCESS_KEYS = 8192


def _with_wildcard(values: Sequence[int], wildcard: int) -> list[int]:
    return list(dict.fromkeys([*values, wildcard]))


def member_masks(
    *,
    org: int,
    regions: Sequence[int],
    roles: Sequence[int],
    groups: Sequence[int],
    depts: Sequence[int],
) -> list[int]:
    """A member's full mask set, built per dimension: (own values ∪ {MAX}) each.

    The same set as expanding every region × role × group × dept tuple with
    :func:`expand_wildcards`, but built directly — ``(r+1)(ro+1)(g+1)(d+1)``
    masks, no duplicates to strip — so the cost tracks the result size. Only valid
    for a *full* product of assignments (which is what a membership asserts); for an
    arbitrary list of masks use :func:`expand_member_masks`. The first mask is the
    member's own first tuple; the org is never wildcarded.
    """
    return [
        encode(org=org, region=r, role=ro, group=g, dept=d)
        for r in _with_wildcard(regions, MAX_REGION)
        for ro in _with_wildcard(roles, MAX_ROLE)
        for g in _with_wildcard(groups, MAX_GROUP)
        for d in _with_wildcard(depts, MAX_DEPT)
    ]


def _field_matches(user_val: int, doc_val: int, wildcard: int) -> bool:
    """A document field matches if it's the wildcard value OR equals the user value."""
    return doc_val == wildcard or user_val == doc_val


def _validate_range(name: str, value: int, max_val: int) -> None:
    if not 0 <= value <= max_val:
        msg = f"{name}={value} out of range [0, {max_val}]"
        raise ValueError(msg)
