"""The agent's element reference must cover the element vocabulary.

`describe_form_elements` is a hand-written catalog, so it drifts silently: an
element type missing from it is one the authoring agent cannot use (it has no
other description of the shape), and a stale key describes something that no
longer exists. This pins it to the discriminated union — the schema is the
source of truth, this reference follows it.
"""

from __future__ import annotations

import typing

import pytest
from api.schemas.form_elements import FormElement
from api.services.agent import AgentService


def _union_types() -> set[str]:
    """Every `type` discriminator value in the FormElement union."""
    annotated_args = typing.get_args(FormElement)
    union = annotated_args[0]
    out: set[str] = set()
    for member in typing.get_args(union):
        field = member.model_fields["type"]
        out.update(typing.get_args(field.annotation))
    return out


@pytest.mark.asyncio
async def test_catalog_covers_every_element_type():
    service = AgentService.__new__(AgentService)  # no DB / org needed for this tool
    described = await AgentService._tool_describe_form_elements(service, None, {})
    documented = set(described["elements"])
    missing = _union_types() - documented
    unknown = documented - _union_types()
    assert not missing, f"element types with no agent reference: {sorted(missing)}"
    assert not unknown, f"agent reference describes non-existent types: {sorted(unknown)}"
