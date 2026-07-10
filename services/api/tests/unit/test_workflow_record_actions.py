"""Unit tests for the record read/write workflow actions: ``get_record``
(read a record's live fields into ``vars``) and ``update_record`` (targeted,
multi-field write). These are the primitives a "mission state" workflow uses to
track and react to shared state."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from api.services.workflow.actions import ACTION_REGISTRY, ActionContext, ActionError


class FakeRepo:
    """Stand-in for DynamicEntityRepository capturing get/list/update calls."""

    def __init__(self, records: list[dict] | None = None) -> None:
        self.records = records or []
        self.get_calls: list[uuid.UUID] = []
        self.list_calls: list[dict] = []
        self.update_calls: list[tuple[uuid.UUID, dict]] = []

    async def get(self, record_id: uuid.UUID) -> dict | None:
        self.get_calls.append(record_id)
        for r in self.records:
            if str(r.get("id")) == str(record_id):
                return r
        return None

    async def list(self, *, filters=None, search=None, cursor=None, limit=50, order_by=None, order_dir="desc"):
        self.list_calls.append({"filters": filters, "limit": limit, "order_by": order_by, "order_dir": order_dir})
        return list(self.records)[:limit], None

    async def update(self, record_id: uuid.UUID, patch: dict) -> dict:
        self.update_calls.append((record_id, patch))
        return {"id": record_id, **patch}


def _ctx(config, *, repo=None, trigger_repo=None, record_id=None, after=None, before=None, inputs=None, vars=None):
    async def _slug(_slug_name: str):
        return repo

    async def _trig():
        return trigger_repo

    return ActionContext(
        org_id=uuid.uuid4(),
        record_id=record_id,
        before=before,
        after=after or {},
        inputs=inputs or {},
        vars=vars or {},
        config=config,
        trigger_repo=_trig,  # type: ignore[arg-type]
        repo_for_slug=_slug,  # type: ignore[arg-type]
    )


class TestGetRecord:
    @pytest.mark.asyncio
    async def test_by_id_returns_jsonable_fields(self) -> None:
        rid = uuid.uuid4()
        created = dt.datetime(2026, 7, 9, 12, 0, tzinfo=dt.UTC)
        repo = FakeRepo([{"id": rid, "created_at": created, "alert_level": "Red", "shields": Decimal("60.5")}])
        handler = ACTION_REGISTRY["get_record"]
        ctx = _ctx({"target_slug": "mission_state", "record_id": str(rid)}, repo=repo)
        out = await handler.execute(ctx)
        # UUID/datetime/Decimal must be JSON-safe so the engine can store them.
        assert out["id"] == str(rid)
        assert out["created_at"] == created.isoformat()
        assert out["alert_level"] == "Red"
        assert out["shields"] == 60.5
        assert repo.get_calls == [rid]

    @pytest.mark.asyncio
    async def test_default_mode_is_latest_when_no_record_id(self) -> None:
        repo = FakeRepo([{"id": uuid.uuid4(), "phase": "Crisis"}])
        handler = ACTION_REGISTRY["get_record"]
        out = await handler.execute(_ctx({"target_slug": "mission_state"}, repo=repo))
        assert out["phase"] == "Crisis"
        assert repo.list_calls[0] == {"filters": None, "limit": 1, "order_by": "created_at", "order_dir": "desc"}

    @pytest.mark.asyncio
    async def test_first_mode_orders_ascending(self) -> None:
        repo = FakeRepo([{"id": uuid.uuid4(), "phase": "Pre-Launch"}])
        handler = ACTION_REGISTRY["get_record"]
        await handler.execute(_ctx({"target_slug": "mission_state", "mode": "first"}, repo=repo))
        assert repo.list_calls[0]["order_dir"] == "asc"

    @pytest.mark.asyncio
    async def test_missing_record_returns_empty_dict(self) -> None:
        handler = ACTION_REGISTRY["get_record"]
        out = await handler.execute(_ctx({"target_slug": "mission_state", "mode": "latest"}, repo=FakeRepo([])))
        assert out == {}  # a gateway branches on {{ vars.state.id }} being falsy

    @pytest.mark.asyncio
    async def test_latest_honours_filters(self) -> None:
        repo = FakeRepo([{"id": uuid.uuid4()}])
        handler = ACTION_REGISTRY["get_record"]
        cfg = {"target_slug": "mission_state", "filters": {"mission_name": {"$ref": "inputs.name"}}}
        await handler.execute(_ctx(cfg, repo=repo, inputs={"name": "Deep Horizon"}))
        assert repo.list_calls[0]["filters"] == {"mission_name": "Deep Horizon"}

    @pytest.mark.asyncio
    async def test_missing_target_slug_raises(self) -> None:
        handler = ACTION_REGISTRY["get_record"]
        with pytest.raises(ActionError):
            await handler.execute(_ctx({"mode": "latest"}, repo=FakeRepo([])))

    @pytest.mark.asyncio
    async def test_invalid_record_id_raises(self) -> None:
        handler = ACTION_REGISTRY["get_record"]
        with pytest.raises(ActionError):
            await handler.execute(_ctx({"target_slug": "x", "record_id": "not-a-uuid"}, repo=FakeRepo([])))

    def test_simulate_does_not_touch_repo(self) -> None:
        handler = ACTION_REGISTRY["get_record"]
        out = handler.simulate(_ctx({"target_slug": "mission_state", "mode": "latest"}, repo=None))
        assert out["target_slug"] == "mission_state"


class TestUpdateRecord:
    @pytest.mark.asyncio
    async def test_targeted_latest_resolves_id_and_writes_values(self) -> None:
        rid = uuid.uuid4()
        repo = FakeRepo([{"id": rid, "alert_level": "Green"}])
        handler = ACTION_REGISTRY["update_record"]
        ctx = _ctx(
            {
                "target_slug": "mission_state",
                "mode": "latest",
                "values": {"alert_level": {"$ref": "inputs.alert"}, "phase": "Crisis"},
            },
            repo=repo,
            inputs={"alert": "Red"},
        )
        out = await handler.execute(ctx)
        assert repo.update_calls == [(rid, {"alert_level": "Red", "phase": "Crisis"})]
        assert out["updated"] is True
        assert out["record_id"] == str(rid)

    @pytest.mark.asyncio
    async def test_by_id_target(self) -> None:
        rid = uuid.uuid4()
        repo = FakeRepo([{"id": rid}])
        handler = ACTION_REGISTRY["update_record"]
        ctx = _ctx(
            {"target_slug": "mission_state", "record_id": str(rid), "values": {"phase": "Complete"}},
            repo=repo,
        )
        await handler.execute(ctx)
        assert repo.update_calls[0][0] == rid

    @pytest.mark.asyncio
    async def test_defaults_to_triggering_record_when_no_target(self) -> None:
        rid = uuid.uuid4()
        trig = FakeRepo([{"id": rid}])
        handler = ACTION_REGISTRY["update_record"]
        ctx = _ctx(
            {"values": {"status": "done"}},
            trigger_repo=trig,
            record_id=rid,
            after={"id": str(rid)},
        )
        await handler.execute(ctx)
        assert trig.update_calls == [(rid, {"status": "done"})]

    @pytest.mark.asyncio
    async def test_no_target_and_no_trigger_raises(self) -> None:
        handler = ACTION_REGISTRY["update_record"]
        with pytest.raises(ActionError):
            await handler.execute(_ctx({"values": {"a": 1}}))

    @pytest.mark.asyncio
    async def test_empty_values_raises(self) -> None:
        handler = ACTION_REGISTRY["update_record"]
        with pytest.raises(ActionError):
            await handler.execute(_ctx({"target_slug": "x", "values": {}}, repo=FakeRepo([{"id": uuid.uuid4()}])))

    @pytest.mark.asyncio
    async def test_latest_with_no_record_raises(self) -> None:
        handler = ACTION_REGISTRY["update_record"]
        with pytest.raises(ActionError):
            await handler.execute(_ctx({"target_slug": "x", "mode": "latest", "values": {"a": 1}}, repo=FakeRepo([])))


class TestUpdateRecordTemplates:
    """Finish-feature fixes: update_record/get_record must render {{ }} templates,
    not just $ref envelopes, and treat empty-string record_id as absent."""

    @pytest.mark.asyncio
    async def test_update_record_renders_curly_templates(self) -> None:
        # {{ after.x }} in a values map must be rendered, not written literally.
        rid = uuid.uuid4()
        repo = FakeRepo([{"id": rid}])
        handler = ACTION_REGISTRY["update_record"]
        ctx = _ctx(
            {"target_slug": "mission_state", "mode": "latest", "values": {"note": "Hi {{after.name}}"}},
            repo=repo,
            after={"name": "Ada"},
        )
        await handler.execute(ctx)
        assert repo.update_calls == [(rid, {"note": "Hi Ada"})]

    @pytest.mark.asyncio
    async def test_update_record_renders_inputs_template(self) -> None:
        rid = uuid.uuid4()
        repo = FakeRepo([{"id": rid}])
        handler = ACTION_REGISTRY["update_record"]
        ctx = _ctx(
            {"target_slug": "s", "mode": "latest", "values": {"phase": "{{inputs.phase}}"}},
            repo=repo,
            inputs={"phase": "Crisis"},
        )
        await handler.execute(ctx)
        assert repo.update_calls[0][1] == {"phase": "Crisis"}

    @pytest.mark.asyncio
    async def test_get_record_filters_render_templates(self) -> None:
        repo = FakeRepo([{"id": uuid.uuid4()}])
        handler = ACTION_REGISTRY["get_record"]
        ctx = _ctx(
            {"target_slug": "s", "filters": {"mission_name": "{{inputs.name}}"}},
            repo=repo,
            inputs={"name": "Deep Horizon"},
        )
        await handler.execute(ctx)
        assert repo.list_calls[0]["filters"] == {"mission_name": "Deep Horizon"}

    @pytest.mark.asyncio
    async def test_empty_record_id_falls_back_to_latest(self) -> None:
        # An empty-string record_id (e.g. unresolved template) must not force by_id.
        repo = FakeRepo([{"id": uuid.uuid4(), "phase": "Returning"}])
        handler = ACTION_REGISTRY["get_record"]
        out = await handler.execute(_ctx({"target_slug": "s", "record_id": ""}, repo=repo))
        assert out["phase"] == "Returning"
        assert repo.list_calls[0]["order_dir"] == "desc"  # latest, not a by_id error

    @pytest.mark.asyncio
    async def test_update_record_first_mode_orders_ascending(self) -> None:
        repo = FakeRepo([{"id": uuid.uuid4()}])
        handler = ACTION_REGISTRY["update_record"]
        await handler.execute(
            _ctx({"target_slug": "s", "mode": "first", "values": {"a": 1}}, repo=repo)
        )
        assert repo.list_calls[0]["order_dir"] == "asc"

    def test_update_record_simulate_does_not_write(self) -> None:
        repo = FakeRepo([{"id": uuid.uuid4()}])
        handler = ACTION_REGISTRY["update_record"]
        ctx = _ctx(
            {"target_slug": "s", "values": {"alert": {"$ref": "inputs.a"}, "note": "{{after.n}}"}},
            repo=repo,
            inputs={"a": "Red"},
            after={"n": "hi"},
        )
        out = handler.simulate(ctx)
        assert repo.update_calls == []  # dry run touches nothing
        assert out["values"] == {"alert": "Red", "note": "hi"}
