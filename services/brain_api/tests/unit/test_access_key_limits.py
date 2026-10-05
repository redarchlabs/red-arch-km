"""Every brain-api request that takes ``access_keys`` applies the same cap.

Member masks carry their wildcard variants (see access_mask.member_masks), so a
member with many assignments sends many masks. The cap leaves room for that while
bounding the filter, and is the same on every path: an over-long list is a 422
(fail closed), never a silently truncated — and so wider — filter.
"""

from __future__ import annotations

import pytest
from brain_api.limits import MAX_ACCESS_KEYS
from brain_api.routers.agent import AgentAskRequest, GapReExtractRequest
from brain_api.routers.rag import AskRequest
from brain_api.routers.search import VectorChatRequest, VectorSearchRequest
from pydantic import ValidationError

_MODELS = [
    (AskRequest, {"tenant_id": "t", "query": "q"}),
    (VectorSearchRequest, {"tenant_id": "t", "query": "q"}),
    (VectorChatRequest, {"tenant_id": "t", "query": "q"}),
    (AgentAskRequest, {"tenant_id": "t", "query": "q"}),
    (GapReExtractRequest, {"tenant_id": "t", "gap_id": "g"}),
]


def test_cap_matches_the_api_side() -> None:
    from access_mask import MAX_ACCESS_KEYS as API_CAP

    assert MAX_ACCESS_KEYS == API_CAP == 8192


@pytest.mark.parametrize(("model", "base"), _MODELS)
def test_at_the_cap_is_accepted(model, base) -> None:  # noqa: ANN001
    req = model(**base, access_keys=list(range(MAX_ACCESS_KEYS)))
    assert len(req.access_keys) == MAX_ACCESS_KEYS


@pytest.mark.parametrize(("model", "base"), _MODELS)
def test_over_the_cap_is_refused(model, base) -> None:  # noqa: ANN001
    with pytest.raises(ValidationError):
        model(**base, access_keys=list(range(MAX_ACCESS_KEYS + 1)))
