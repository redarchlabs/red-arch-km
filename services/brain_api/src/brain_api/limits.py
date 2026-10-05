"""Request bounds shared by brain-api routers.

``MAX_ACCESS_KEYS`` caps every request's ``access_keys``. A requester's masks are
built per dimension with wildcard variants ((regions+1) x (depts+1) x (roles+1) x
(groups+1) of them, see ``access_mask.member_masks``), so the cap leaves room for
that while bounding the filter. Over it, the request is refused (422) — never
truncated, which would change who can see what. Must equal
``access_mask.MAX_ACCESS_KEYS`` on the API side (brain-api does not depend on that
package; a unit test pins the two together).
"""

MAX_ACCESS_KEYS = 8192
