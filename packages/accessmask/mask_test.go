package accessmask

import (
	"testing"
)

func TestEncode(t *testing.T) {
	tests := []struct {
		name     string
		org      uint16
		region   uint8
		role     uint8
		group    uint8
		dept     uint8
		expected uint32
		wantErr  bool
	}{
		{
			name:     "all zeros",
			expected: 0,
		},
		{
			name:     "org only",
			org:      1,
			expected: 1 << OrgShift,
		},
		{
			name:     "all fields set",
			org:      100,
			region:   5,
			role:     10,
			group:    20,
			dept:     3,
			expected: (100 << OrgShift) | (5 << RegionShift) | (10 << RoleShift) | (20 << GroupShift) | (3 << DeptShift),
		},
		{
			name:     "max values",
			org:      MaxOrgID,
			region:   MaxRegion,
			role:     MaxRole,
			group:    MaxGroup,
			dept:     MaxDept,
			expected: 0xFFFFFFFF,
		},
		{
			name:    "org out of range",
			org:     MaxOrgID + 1,
			wantErr: true,
		},
		{
			name:    "region out of range",
			region:  MaxRegion + 1,
			wantErr: true,
		},
		{
			name:    "role out of range",
			role:    MaxRole + 1,
			wantErr: true,
		},
		{
			name:    "group out of range",
			group:   MaxGroup + 1,
			wantErr: true,
		},
		{
			name:    "dept out of range",
			dept:    MaxDept + 1,
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result, err := Encode(tt.org, tt.region, tt.role, tt.group, tt.dept)
			if tt.wantErr {
				if err == nil {
					t.Error("expected error, got nil")
				}
				return
			}
			if err != nil {
				t.Errorf("unexpected error: %v", err)
				return
			}
			if result != tt.expected {
				t.Errorf("Encode() = %d (0x%08X), want %d (0x%08X)", result, result, tt.expected, tt.expected)
			}
		})
	}
}

func TestDecode(t *testing.T) {
	tests := []struct {
		name   string
		mask   uint32
		want   DecodedMask
	}{
		{
			name: "all zeros",
			mask: 0,
			want: DecodedMask{},
		},
		{
			name: "org only",
			mask: 100 << OrgShift,
			want: DecodedMask{Org: 100},
		},
		{
			name: "all fields",
			mask: (100 << OrgShift) | (5 << RegionShift) | (10 << RoleShift) | (20 << GroupShift) | (3 << DeptShift),
			want: DecodedMask{Org: 100, Region: 5, Role: 10, Group: 20, Dept: 3},
		},
		{
			name: "max values",
			mask: 0xFFFFFFFF,
			want: DecodedMask{Org: MaxOrgID, Region: MaxRegion, Role: MaxRole, Group: MaxGroup, Dept: MaxDept},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := Decode(tt.mask)
			if got != tt.want {
				t.Errorf("Decode(0x%08X) = %+v, want %+v", tt.mask, got, tt.want)
			}
		})
	}
}

func TestMatches(t *testing.T) {
	tests := []struct {
		name     string
		userMask uint32
		docMask  uint32
		expected bool
	}{
		{
			name:     "exact match",
			userMask: encode(100, 5, 10, 20, 3),
			docMask:  encode(100, 5, 10, 20, 3),
			expected: true,
		},
		{
			name:     "org mismatch",
			userMask: encode(100, 5, 10, 20, 3),
			docMask:  encode(101, 5, 10, 20, 3),
			expected: false,
		},
		{
			name:     "region wildcard in doc",
			userMask: encode(100, 5, 10, 20, 3),
			docMask:  encode(100, MaxRegion, 10, 20, 3),
			expected: true,
		},
		{
			name:     "role wildcard in doc",
			userMask: encode(100, 5, 10, 20, 3),
			docMask:  encode(100, 5, MaxRole, 20, 3),
			expected: true,
		},
		{
			name:     "group wildcard in doc",
			userMask: encode(100, 5, 10, 20, 3),
			docMask:  encode(100, 5, 10, MaxGroup, 3),
			expected: true,
		},
		{
			name:     "dept wildcard in doc",
			userMask: encode(100, 5, 10, 20, 3),
			docMask:  encode(100, 5, 10, 20, MaxDept),
			expected: true,
		},
		{
			name:     "all wildcards except org",
			userMask: encode(100, 5, 10, 20, 3),
			docMask:  encode(100, MaxRegion, MaxRole, MaxGroup, MaxDept),
			expected: true,
		},
		{
			name:     "region mismatch no wildcard",
			userMask: encode(100, 5, 10, 20, 3),
			docMask:  encode(100, 6, 10, 20, 3),
			expected: false,
		},
		{
			name:     "role mismatch no wildcard",
			userMask: encode(100, 5, 10, 20, 3),
			docMask:  encode(100, 5, 11, 20, 3),
			expected: false,
		},
		{
			name:     "group mismatch no wildcard",
			userMask: encode(100, 5, 10, 20, 3),
			docMask:  encode(100, 5, 10, 21, 3),
			expected: false,
		},
		{
			name:     "dept mismatch no wildcard",
			userMask: encode(100, 5, 10, 20, 3),
			docMask:  encode(100, 5, 10, 20, 4),
			expected: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := Matches(tt.userMask, tt.docMask)
			if got != tt.expected {
				t.Errorf("Matches(user=0x%08X, doc=0x%08X) = %v, want %v", tt.userMask, tt.docMask, got, tt.expected)
			}
		})
	}
}

func TestAccessMaskType(t *testing.T) {
	m, err := NewAccessMask(100, 5, 10, 20, 3)
	if err != nil {
		t.Fatalf("NewAccessMask() error = %v", err)
	}

	decoded := m.Decoded()
	if decoded.Org != 100 || decoded.Region != 5 || decoded.Role != 10 || decoded.Group != 20 || decoded.Dept != 3 {
		t.Errorf("Decoded() = %+v, want {Org:100 Region:5 Role:10 Group:20 Dept:3}", decoded)
	}

	docMask, _ := NewAccessMask(100, 5, 10, 20, 3)
	if !m.Matches(docMask) {
		t.Error("expected masks to match")
	}

	otherOrg, _ := NewAccessMask(101, 5, 10, 20, 3)
	if m.Matches(otherOrg) {
		t.Error("expected different orgs not to match")
	}
}

func TestRoundTrip(t *testing.T) {
	testCases := []struct {
		org    uint16
		region uint8
		role   uint8
		group  uint8
		dept   uint8
	}{
		{0, 0, 0, 0, 0},
		{1, 2, 3, 4, 5},
		{100, 5, 10, 20, 3},
		{MaxOrgID, MaxRegion, MaxRole, MaxGroup, MaxDept},
		{1234, 15, 20, 60, 8},
	}

	for _, tc := range testCases {
		encoded, err := Encode(tc.org, tc.region, tc.role, tc.group, tc.dept)
		if err != nil {
			t.Errorf("Encode(%d, %d, %d, %d, %d) error = %v", tc.org, tc.region, tc.role, tc.group, tc.dept, err)
			continue
		}
		decoded := Decode(encoded)
		if decoded.Org != tc.org || decoded.Region != tc.region || decoded.Role != tc.role || decoded.Group != tc.group || decoded.Dept != tc.dept {
			t.Errorf("Round trip failed: input (%d, %d, %d, %d, %d), got %+v", tc.org, tc.region, tc.role, tc.group, tc.dept, decoded)
		}
	}
}

// encode is a test helper that panics on error
func encode(org uint16, region, role, group, dept uint8) uint32 {
	m, err := Encode(org, region, role, group, dept)
	if err != nil {
		panic(err)
	}
	return m
}

func TestExpandWildcards(t *testing.T) {
	m, _ := Encode(9, 3, 2, 7, 5)
	variants := ExpandWildcards(m)
	if len(variants) != 16 {
		t.Fatalf("want 16 variants, got %d", len(variants))
	}
	if variants[0] != m {
		t.Fatalf("original mask must come first")
	}
	seen := map[uint32]bool{}
	for _, v := range variants {
		if seen[v] {
			t.Fatalf("duplicate variant %d", v)
		}
		seen[v] = true
		d := Decode(v)
		if d.Org != 9 {
			t.Fatalf("org must never be wildcarded, got %d", d.Org)
		}
		if (d.Region != 3 && d.Region != MaxRegion) || (d.Role != 2 && d.Role != MaxRole) ||
			(d.Group != 7 && d.Group != MaxGroup) || (d.Dept != 5 && d.Dept != MaxDept) {
			t.Fatalf("unexpected variant %+v", d)
		}
	}
}

func TestExpandWildcardsDedupesMaxValues(t *testing.T) {
	m, _ := Encode(9, MaxRegion, 2, 7, 5)
	if got := len(ExpandWildcards(m)); got != 8 {
		t.Fatalf("want 8 variants, got %d", got)
	}
}

func TestExpandMemberMasks(t *testing.T) {
	a, _ := Encode(9, 3, 0, 0, 5)
	b, _ := Encode(9, 4, 0, 0, 5)
	out := ExpandMemberMasks([]uint32{a, b, a})
	if out[0] != a {
		t.Fatalf("order not kept")
	}
	// 16 + 16, minus the 8 region-wildcard variants the two masks share.
	if len(out) != 24 {
		t.Fatalf("want 24 distinct masks, got %d", len(out))
	}
	if len(ExpandMemberMasks(nil)) != 0 {
		t.Fatalf("empty in, empty out")
	}
}

// Equality against the expanded set must agree with Matches for every pair.
func TestExpandWildcardsAgreesWithMatches(t *testing.T) {
	regions := []uint8{0, 3, 4, MaxRegion}
	roles := []uint8{0, 2, MaxRole}
	groups := []uint8{0, 7, MaxGroup}
	depts := []uint8{0, 5, 6, MaxDept}
	for _, ur := range []uint8{0, 3} {
		for _, ud := range []uint8{5, 6} {
			for _, ug := range []uint8{0, 7} {
				user, _ := Encode(9, ur, 2, ug, ud)
				expanded := map[uint32]bool{}
				for _, v := range ExpandWildcards(user) {
					expanded[v] = true
				}
				for _, org := range []uint16{9, 10} {
					for _, r := range regions {
						for _, ro := range roles {
							for _, g := range groups {
								for _, d := range depts {
									doc, _ := Encode(org, r, ro, g, d)
									if expanded[doc] != Matches(user, doc) {
										t.Fatalf("user %+v doc %+v: set=%v matches=%v",
											Decode(user), Decode(doc), expanded[doc], Matches(user, doc))
									}
								}
							}
						}
					}
				}
			}
		}
	}
}

func TestMemberMasksMatchesPerTupleExpansion(t *testing.T) {
	regions, roles, groups, depts := []uint8{3, 4}, []uint8{2}, []uint8{7, 8, 9}, []uint8{5, 6}
	var base []uint32
	for _, r := range regions {
		for _, ro := range roles {
			for _, g := range groups {
				for _, d := range depts {
					m, _ := Encode(9, r, ro, g, d)
					base = append(base, m)
				}
			}
		}
	}
	want := map[uint32]bool{}
	for _, v := range ExpandMemberMasks(base) {
		want[v] = true
	}
	got := MemberMasks(9, regions, roles, groups, depts)
	if len(got) != len(want) {
		t.Fatalf("got %d masks, want %d", len(got), len(want))
	}
	for _, v := range got {
		if !want[v] {
			t.Fatalf("unexpected mask %+v", Decode(v))
		}
	}
	if len(got) != 3*2*4*3 {
		t.Fatalf("size should be the product of (count+1), got %d", len(got))
	}
	first, _ := Encode(9, 3, 2, 7, 5)
	if got[0] != first {
		t.Fatalf("base tuple must come first")
	}
	if MaxAccessKeys != 8192 {
		t.Fatalf("MaxAccessKeys = %d", MaxAccessKeys)
	}
}
