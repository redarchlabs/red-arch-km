"""Unit tests for the native record tools: registration + authority behavior.

These are the tools that let an agent read and mutate the org's custom-entity
data (the task tracker, research items, KPIs, ...). The record writes must obey
the same governance as any other WRITE tool:

* reads are always available to every kind;
* writes are operator-only (kind-gate) AND require ``records_write``;
* writes are *internal* (not side-effecting), so a high-touch org does not gate
  them behind human approval — only outbound actions are gated.
"""

from __future__ import annotations

import pytest

from api.models.agent import Agent
from api.services.agents.authority import Decision, decide
from api.services.agents.tools.documents import CREATE_DOCUMENT
from api.services.agents.tools.records import (
    CREATE_RECORD,
    GET_RECORD,
    LIST_RECORDS,
    UPDATE_RECORD,
)
from api.services.agents.tools.registry import base_tool_specs
from api.services.agents.tools.spec import Category

pytestmark = pytest.mark.unit


def _agent(kind: str, **grants) -> Agent:
    return Agent(name="a", provider="openai", model="gpt-5-mini", kind=kind, grants=grants)


def test_read_record_tools_are_always_allowed_reads() -> None:
    for spec in (LIST_RECORDS, GET_RECORD):
        assert spec.category == Category.READ
        assert spec.always_allowed is True
        for kind in ("coordinator", "advisory", "operator"):
            assert decide(_agent(kind), spec).decision is Decision.ALLOW


def test_write_tools_are_internal_writes() -> None:
    for spec in (CREATE_RECORD, UPDATE_RECORD, CREATE_DOCUMENT):
        assert spec.category == Category.WRITE
        # An internal write, not an external egress — so it is never side-effecting.
        assert spec.side_effecting is False
        assert spec.always_allowed is False


@pytest.mark.parametrize("spec", [CREATE_RECORD, UPDATE_RECORD, CREATE_DOCUMENT])
def test_write_tools_gated_by_kind_and_records_write(spec) -> None:
    # advisory & coordinator: the kind-gate denies WRITE regardless of grants.
    for kind in ("advisory", "coordinator"):
        agent = _agent(kind, tools=[spec.name], records_write=True)
        assert decide(agent, spec).decision is Decision.DENY
    # operator granted the tool but WITHOUT records_write: denied.
    assert decide(_agent("operator", tools=[spec.name]), spec).decision is Decision.DENY
    # operator with the tool granted + records_write: allowed, and no approval (internal).
    granted = _agent("operator", tools=[spec.name], records_write=True)
    assert decide(granted, spec).decision is Decision.ALLOW


def test_record_tools_registered_in_base_set() -> None:
    names = {s.name for s in base_tool_specs()}
    assert {
        "list_records",
        "get_record",
        "create_record",
        "update_record",
        "create_document",
    } <= names
