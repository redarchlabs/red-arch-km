"""AccessMask: encode, decode, and match 32-bit RBAC permission masks."""

from __future__ import annotations

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


def _field_matches(user_val: int, doc_val: int, wildcard: int) -> bool:
    """A document field matches if it's the wildcard value OR equals the user value."""
    return doc_val == wildcard or user_val == doc_val


def _validate_range(name: str, value: int, max_val: int) -> None:
    if not 0 <= value <= max_val:
        msg = f"{name}={value} out of range [0, {max_val}]"
        raise ValueError(msg)
