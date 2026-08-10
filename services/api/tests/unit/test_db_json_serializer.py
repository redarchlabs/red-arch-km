"""What the JSONB serializer must tolerate.

Every value that reaches a JSONB column passes through here: entity record values
on their way into ``workflow_outbox.after_data``, and every agent tool result on
its way into ``agent_run_steps.content``. A type this refuses does not degrade
gracefully — the INSERT raises, and for an agent run the failing insert *is* the
step record, so the run dies with no trace of what it was doing.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
from api.db import json_serializer

pytestmark = pytest.mark.unit


class TestValuesThatFlowThroughJsonbColumns:
    def test_a_uuid_serializes_as_its_string_form(self) -> None:
        """An agent's ``list_records`` result carries each row's id. This raised
        'Object of type UUID is not JSON serializable' and finalised the run as
        'execution failed' the first time an agent listed records that existed."""
        record_id = uuid.uuid4()

        assert json.loads(json_serializer({"id": record_id}))["id"] == str(record_id)

    def test_a_decimal_serializes_as_a_number(self) -> None:
        assert json.loads(json_serializer({"total": Decimal("12.50")}))["total"] == 12.5

    def test_datetimes_and_dates_serialize_as_iso_strings(self) -> None:
        moment = datetime(2026, 8, 10, 9, 30)
        day = date(2026, 8, 10)

        out = json.loads(json_serializer({"at": moment, "on": day}))

        assert out == {"at": moment.isoformat(), "on": day.isoformat()}

    def test_a_nested_record_list_survives(self) -> None:
        """The real shape: a tool result wrapping rows that mix all of these."""
        rows = [{"id": uuid.uuid4(), "amount": Decimal("3.25"), "created_at": datetime(2026, 8, 10)}]

        out = json.loads(json_serializer({"result": {"records": rows}}))

        assert out["result"]["records"][0]["amount"] == 3.25

    def test_an_unsupported_type_still_raises(self) -> None:
        """The guard stays a guard — silently dropping a value would write a
        record that looks complete and is not."""
        with pytest.raises(TypeError, match="not JSON serializable"):
            json_serializer({"nope": object()})
