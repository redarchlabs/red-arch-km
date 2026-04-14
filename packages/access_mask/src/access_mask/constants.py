"""
32-bit access mask bit layout:

    [ORG_ID (11)] [REGION (5)] [ROLE (5)] [GROUP (7)] [DEPT (4)]
    ├─ bits 21-31 ─┤─ 16-20 ──┤─ 11-15 ─┤── 4-10 ──┤── 0-3 ─┤

Total: 11 + 5 + 5 + 7 + 4 = 32 bits
"""

# Bit widths
ORG_BITS: int = 11
REGION_BITS: int = 5
ROLE_BITS: int = 5
GROUP_BITS: int = 7
DEPT_BITS: int = 4

# Maximum values (all bits set = wildcard)
MAX_ORG_ID: int = (1 << ORG_BITS) - 1    # 2047
MAX_REGION: int = (1 << REGION_BITS) - 1  # 31
MAX_ROLE: int = (1 << ROLE_BITS) - 1      # 31
MAX_GROUP: int = (1 << GROUP_BITS) - 1    # 127
MAX_DEPT: int = (1 << DEPT_BITS) - 1      # 15

# Bit shifts
ORG_SHIFT: int = REGION_BITS + ROLE_BITS + GROUP_BITS + DEPT_BITS  # 21
REGION_SHIFT: int = ROLE_BITS + GROUP_BITS + DEPT_BITS              # 16
ROLE_SHIFT: int = GROUP_BITS + DEPT_BITS                            # 11
GROUP_SHIFT: int = DEPT_BITS                                        # 4
DEPT_SHIFT: int = 0
