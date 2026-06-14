// Package accessmask provides encoding, decoding, and matching for 32-bit RBAC
// permission masks used in Red Arch Knowledge Manager.
//
// Bit layout (32 bits total):
//
//	[ORG_ID (11)] [REGION (5)] [ROLE (5)] [GROUP (7)] [DEPT (4)]
//	├─ bits 21-31 ─┤─ 16-20 ──┤─ 11-15 ─┤── 4-10 ──┤── 0-3 ─┤
package accessmask

import (
	"fmt"
)

// Bit widths for each field.
const (
	OrgBits    = 11
	RegionBits = 5
	RoleBits   = 5
	GroupBits  = 7
	DeptBits   = 4
)

// Maximum values (all bits set acts as wildcard for matching).
const (
	MaxOrgID  uint16 = (1 << OrgBits) - 1  // 2047
	MaxRegion uint8  = (1 << RegionBits) - 1 // 31
	MaxRole   uint8  = (1 << RoleBits) - 1   // 31
	MaxGroup  uint8  = (1 << GroupBits) - 1  // 127
	MaxDept   uint8  = (1 << DeptBits) - 1   // 15
)

// Bit shifts for each field.
const (
	DeptShift   = 0
	GroupShift  = DeptBits                                   // 4
	RoleShift   = GroupBits + DeptBits                       // 11
	RegionShift = RoleBits + GroupBits + DeptBits            // 16
	OrgShift    = RegionBits + RoleBits + GroupBits + DeptBits // 21
)

// DecodedMask represents the individual permission components.
type DecodedMask struct {
	Org    uint16
	Region uint8
	Role   uint8
	Group  uint8
	Dept   uint8
}

// AccessMask wraps a 32-bit integer access mask with decode/match helpers.
type AccessMask struct {
	value uint32
}

// NewAccessMask creates an AccessMask from permission components.
// Returns an error if any component is out of range.
func NewAccessMask(org uint16, region, role, group, dept uint8) (AccessMask, error) {
	v, err := Encode(org, region, role, group, dept)
	if err != nil {
		return AccessMask{}, err
	}
	return AccessMask{value: v}, nil
}

// AccessMaskFromValue creates an AccessMask from a raw 32-bit value.
func AccessMaskFromValue(v uint32) AccessMask {
	return AccessMask{value: v}
}

// Value returns the raw 32-bit mask value.
func (m AccessMask) Value() uint32 {
	return m.value
}

// Decoded returns the individual permission components.
func (m AccessMask) Decoded() DecodedMask {
	return Decode(m.value)
}

// Matches checks if this user mask grants access to the given document mask.
func (m AccessMask) Matches(docMask AccessMask) bool {
	return Matches(m.value, docMask.value)
}

// Encode encodes permission components into a 32-bit integer.
// Returns an error if any component is out of range.
func Encode(org uint16, region, role, group, dept uint8) (uint32, error) {
	if org > MaxOrgID {
		return 0, fmt.Errorf("org=%d out of range [0, %d]", org, MaxOrgID)
	}
	if region > MaxRegion {
		return 0, fmt.Errorf("region=%d out of range [0, %d]", region, MaxRegion)
	}
	if role > MaxRole {
		return 0, fmt.Errorf("role=%d out of range [0, %d]", role, MaxRole)
	}
	if group > MaxGroup {
		return 0, fmt.Errorf("group=%d out of range [0, %d]", group, MaxGroup)
	}
	if dept > MaxDept {
		return 0, fmt.Errorf("dept=%d out of range [0, %d]", dept, MaxDept)
	}

	return (uint32(org) << OrgShift) |
		(uint32(region) << RegionShift) |
		(uint32(role) << RoleShift) |
		(uint32(group) << GroupShift) |
		(uint32(dept) << DeptShift), nil
}

// Decode extracts permission components from a 32-bit mask.
func Decode(mask uint32) DecodedMask {
	return DecodedMask{
		Org:    uint16((mask >> OrgShift) & uint32(MaxOrgID)),
		Region: uint8((mask >> RegionShift) & uint32(MaxRegion)),
		Role:   uint8((mask >> RoleShift) & uint32(MaxRole)),
		Group:  uint8((mask >> GroupShift) & uint32(MaxGroup)),
		Dept:   uint8((mask >> DeptShift) & uint32(MaxDept)),
	}
}

// Matches checks if a user mask grants access to a document mask.
// A document field set to its MAX value acts as a wildcard (any user value matches).
// The org field must always match exactly (no wildcard).
func Matches(userMask, docMask uint32) bool {
	u := Decode(userMask)
	d := Decode(docMask)

	// Org must always match exactly
	if u.Org != d.Org {
		return false
	}

	return fieldMatches(u.Region, d.Region, MaxRegion) &&
		fieldMatches(u.Role, d.Role, MaxRole) &&
		fieldMatches(u.Group, d.Group, MaxGroup) &&
		fieldMatches(u.Dept, d.Dept, MaxDept)
}

// fieldMatches returns true if the document field is a wildcard OR equals the user value.
func fieldMatches[T comparable](userVal, docVal, wildcard T) bool {
	return docVal == wildcard || userVal == docVal
}
