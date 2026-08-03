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


class _PagingRepo:
    """Repo stub that emulates the real keyset pagination (cursor + capped page) so a
    grader that loops the cursor can be exercised over more than one page."""

    def __init__(self, records: list[dict], *, page: int = 200) -> None:
        self.records = records
        self.page = page

    async def list(self, *, filters=None, search=None, cursor=None, limit=50, order_by=None, order_dir="desc"):
        start = cursor or 0
        stop = start + min(limit, self.page)
        rows = self.records[start:stop]
        next_cursor = stop if stop < len(self.records) else None
        return rows, next_cursor


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
        await handler.execute(_ctx({"target_slug": "s", "mode": "first", "values": {"a": 1}}, repo=repo))
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


class TestGradeQuiz:
    """Server-side MCQ grading: compares learner answers to each question's stored
    correct_answer — answers never leave the server. Answers arrive either as a
    ``{question_id: choice}`` map (preferred, order-independent) or positional
    ``a1..aN`` inputs (fallback)."""

    @staticmethod
    def _questions() -> list[dict]:
        return [
            {"id": uuid.uuid4(), "sort_order": 1, "correct_answer": "B"},
            {"id": uuid.uuid4(), "sort_order": 2, "correct_answer": "A"},
            {"id": uuid.uuid4(), "sort_order": 3, "correct_answer": "D"},
        ]

    @pytest.mark.asyncio
    async def test_grades_server_side_and_applies_threshold(self) -> None:
        handler = ACTION_REGISTRY["grade_quiz"]
        repo = FakeRepo(self._questions())
        aid = str(uuid.uuid4())
        ctx = _ctx(
            {"assessment_id": {"$ref": "inputs.assessment_id"}, "pass_threshold": 60},
            repo=repo,
            inputs={"assessment_id": aid, "a1": "B", "a2": "wrong", "a3": "D"},  # 2 of 3
        )
        out = await handler.execute(ctx)
        assert out == {"score": 67, "passed": True, "correct": 2, "total": 3, "answered": 3}
        # Questions loaded by the assessment relation, ordered by sort_order.
        assert repo.list_calls[0]["filters"] == {"assessment": aid}
        assert repo.list_calls[0]["order_by"] == "sort_order"

    @pytest.mark.asyncio
    async def test_grades_by_question_id_map_ignores_order(self) -> None:
        # The preferred id-keyed form matches on question id, so a view that renders
        # questions in a different order (or a sort_order tie) still grades correctly.
        handler = ACTION_REGISTRY["grade_quiz"]
        questions = self._questions()
        repo = FakeRepo(questions)
        answers = {str(questions[0]["id"]): "B", str(questions[1]["id"]): "A", str(questions[2]["id"]): "X"}
        out = await handler.execute(
            _ctx(
                {"assessment_id": "a", "answers": {"$ref": "inputs.answers"}, "pass_threshold": 60},
                repo=repo,
                inputs={"answers": answers},
            ),
        )
        assert out == {"score": 67, "passed": True, "correct": 2, "total": 3, "answered": 3}

    @pytest.mark.asyncio
    async def test_answered_count_detects_partial_submission(self) -> None:
        # answered < total is the signal a miswired view under-supplied answers.
        handler = ACTION_REGISTRY["grade_quiz"]
        repo = FakeRepo(self._questions())
        out = await handler.execute(_ctx({"assessment_id": "a"}, repo=repo, inputs={"a1": "B", "a2": "", "a3": "D"}))
        assert out["answered"] == 2 and out["total"] == 3 and out["correct"] == 2

    @pytest.mark.asyncio
    async def test_threshold_not_met_is_not_passed(self) -> None:
        handler = ACTION_REGISTRY["grade_quiz"]
        repo = FakeRepo(self._questions())
        out = await handler.execute(
            _ctx({"assessment_id": "a", "pass_threshold": 70}, repo=repo, inputs={"a1": "B"})  # 1 of 3
        )
        assert out["score"] == 33 and out["passed"] is False

    @pytest.mark.asyncio
    async def test_missing_answers_count_wrong_default_threshold(self) -> None:
        handler = ACTION_REGISTRY["grade_quiz"]
        repo = FakeRepo(self._questions())
        # No answers → 0 correct; default threshold 70 → not passed.
        out = await handler.execute(_ctx({"assessment_id": "a"}, repo=repo, inputs={}))
        assert out == {"score": 0, "passed": False, "correct": 0, "total": 3, "answered": 0}

    @pytest.mark.asyncio
    async def test_no_questions_scores_zero(self) -> None:
        handler = ACTION_REGISTRY["grade_quiz"]
        out = await handler.execute(_ctx({"assessment_id": "a"}, repo=FakeRepo([]), inputs={"a1": "x"}))
        assert out == {"score": 0, "passed": False, "correct": 0, "total": 0, "answered": 0}

    @pytest.mark.asyncio
    async def test_answer_match_is_trimmed_exact(self) -> None:
        handler = ACTION_REGISTRY["grade_quiz"]
        repo = FakeRepo([{"id": uuid.uuid4(), "sort_order": 1, "correct_answer": " Yes "}])
        out = await handler.execute(_ctx({"assessment_id": "a", "pass_threshold": 1}, repo=repo, inputs={"a1": "Yes"}))
        assert out["correct"] == 1

    @pytest.mark.asyncio
    async def test_pages_beyond_a_single_repo_page(self) -> None:
        # >200 questions must all be graded: the repo caps a page at 200, so grading
        # loops the keyset cursor instead of truncating.
        handler = ACTION_REGISTRY["grade_quiz"]
        questions = [{"id": uuid.uuid4(), "sort_order": n, "correct_answer": "A"} for n in range(250)]
        repo = _PagingRepo(questions, page=200)
        inputs = {f"a{n + 1}": "A" for n in range(250)}
        out = await handler.execute(_ctx({"assessment_id": "a", "pass_threshold": 1}, repo=repo, inputs=inputs))
        assert out["total"] == 250 and out["correct"] == 250

    @pytest.mark.asyncio
    async def test_missing_assessment_id_raises(self) -> None:
        handler = ACTION_REGISTRY["grade_quiz"]
        with pytest.raises(ActionError):
            await handler.execute(_ctx({}, repo=FakeRepo([]), inputs={}))


class TestUpdateRecordIncrements:
    """`increments` is the counter/gauge primitive: a workflow can add to a field's
    CURRENT value (score, fuel, attempt counts) which a `{{ }}` template can't express."""

    @pytest.mark.asyncio
    async def test_delta_is_added_to_the_stored_value(self) -> None:
        rid = uuid.uuid4()
        repo = FakeRepo([{"id": rid, "score": 40, "solved": 2}])
        handler = ACTION_REGISTRY["update_record"]
        ctx = _ctx(
            {
                "target_slug": "mission_run",
                "record_id": str(rid),
                "increments": {"score": {"$ref": "vars.p.points"}, "solved": 1},
            },
            repo=repo,
            vars={"p": {"points": 15}},
        )
        await handler.execute(ctx)
        _, patch = repo.update_calls[0]
        assert patch == {"score": 55, "solved": 3}

    @pytest.mark.asyncio
    async def test_clamp_bounds_the_result(self) -> None:
        rid = uuid.uuid4()
        repo = FakeRepo([{"id": rid, "shields": 10, "hull": 95}])
        handler = ACTION_REGISTRY["update_record"]
        ctx = _ctx(
            {
                "target_slug": "mission_run",
                "record_id": str(rid),
                "increments": {"shields": -20, "hull": 20},
                "clamp": {"shields": [0, 100], "hull": [0, 100]},
            },
            repo=repo,
        )
        await handler.execute(ctx)
        _, patch = repo.update_calls[0]
        assert patch == {"shields": 0, "hull": 100}  # neither runs off the gauge

    @pytest.mark.asyncio
    async def test_missing_current_value_counts_as_zero(self) -> None:
        rid = uuid.uuid4()
        repo = FakeRepo([{"id": rid}])
        handler = ACTION_REGISTRY["update_record"]
        ctx = _ctx({"target_slug": "s", "record_id": str(rid), "increments": {"score": 10}}, repo=repo)
        await handler.execute(ctx)
        assert repo.update_calls[0][1] == {"score": 10}

    @pytest.mark.asyncio
    async def test_unresolvable_delta_is_skipped_not_zeroed(self) -> None:
        # An unset `{{ inputs.bonus }}` must not wipe the field it was meant to bump.
        rid = uuid.uuid4()
        repo = FakeRepo([{"id": rid, "score": 40}])
        handler = ACTION_REGISTRY["update_record"]
        ctx = _ctx(
            {"target_slug": "s", "record_id": str(rid), "increments": {"score": "{{ inputs.bonus }}"}},
            repo=repo,
            inputs={},
        )
        out = await handler.execute(ctx)
        # Nothing resolved, so the row is not touched AT ALL — an empty patch would still
        # bump `updated_at` and emit a record-change event for a write that says nothing.
        assert repo.update_calls == []
        assert out["updated"] is False

    @pytest.mark.asyncio
    async def test_explicit_values_win_over_increments(self) -> None:
        rid = uuid.uuid4()
        repo = FakeRepo([{"id": rid, "score": 40}])
        handler = ACTION_REGISTRY["update_record"]
        ctx = _ctx(
            {"target_slug": "s", "record_id": str(rid), "values": {"score": 0}, "increments": {"score": 10}},
            repo=repo,
        )
        await handler.execute(ctx)
        assert repo.update_calls[0][1] == {"score": 0}  # a reset beats a bump

    @pytest.mark.asyncio
    async def test_increments_alone_satisfy_the_non_empty_requirement(self) -> None:
        rid = uuid.uuid4()
        repo = FakeRepo([{"id": rid, "n": 1}])
        handler = ACTION_REGISTRY["update_record"]
        await handler.execute(_ctx({"target_slug": "s", "record_id": str(rid), "increments": {"n": 1}}, repo=repo))
        assert repo.update_calls[0][1] == {"n": 2}

    @pytest.mark.asyncio
    async def test_empty_config_still_raises(self) -> None:
        handler = ACTION_REGISTRY["update_record"]
        with pytest.raises(ActionError):
            await handler.execute(_ctx({"target_slug": "s", "values": {}, "increments": {}}, repo=FakeRepo([])))

    @pytest.mark.asyncio
    async def test_fractional_delta_keeps_precision(self) -> None:
        rid = uuid.uuid4()
        repo = FakeRepo([{"id": rid, "level": Decimal("2.5")}])
        handler = ACTION_REGISTRY["update_record"]
        await handler.execute(_ctx({"target_slug": "s", "record_id": str(rid), "increments": {"level": 0.25}}, repo=repo))
        assert repo.update_calls[0][1] == {"level": 2.75}


class TestRandomAction:
    """`random` is the variety primitive — without it a workflow can only ever do the
    same thing in the same order, so nothing can feel unpredictable."""

    @pytest.mark.asyncio
    async def test_rolls_within_an_inclusive_range(self) -> None:
        handler = ACTION_REGISTRY["random"]
        for _ in range(50):
            out = await handler.execute(_ctx({"min": 1, "max": 6}))
            assert 1 <= out["value"] <= 6
            assert out["min"] == 1 and out["max"] == 6

    @pytest.mark.asyncio
    async def test_defaults_to_a_percentage_roll(self) -> None:
        out = await ACTION_REGISTRY["random"].execute(_ctx({}))
        assert 1 <= out["value"] <= 100
        assert out["min"] == 1 and out["max"] == 100

    @pytest.mark.asyncio
    async def test_seed_makes_the_roll_reproducible(self) -> None:
        handler = ACTION_REGISTRY["random"]
        first = await handler.execute(_ctx({"min": 1, "max": 1000, "seed": "deep-horizon"}))
        again = await handler.execute(_ctx({"min": 1, "max": 1000, "seed": "deep-horizon"}))
        assert first["value"] == again["value"]

    @pytest.mark.asyncio
    async def test_choices_pick_is_one_based_for_sequence_lookups(self) -> None:
        handler = ACTION_REGISTRY["random"]
        out = await handler.execute(_ctx({"choices": ["asteroids", "boarders", "flare"], "seed": 7}))
        assert out["choice"] in ("asteroids", "boarders", "flare")
        assert out["value"] == out["index"] + 1  # value indexes a 1-based `sequence`
        assert out["max"] == 3

    @pytest.mark.asyncio
    async def test_bounds_resolve_from_run_values(self) -> None:
        # The bank size is only known at run time (e.g. a counted hazard table).
        out = await ACTION_REGISTRY["random"].execute(
            _ctx({"min": 1, "max": {"$ref": "vars.n.count"}}, vars={"n": {"count": 4}})
        )
        assert 1 <= out["value"] <= 4 and out["max"] == 4

    @pytest.mark.asyncio
    async def test_inverted_bounds_are_tolerated(self) -> None:
        out = await ACTION_REGISTRY["random"].execute(_ctx({"min": 10, "max": 2}))
        assert 2 <= out["value"] <= 10

    @pytest.mark.asyncio
    async def test_empty_choices_falls_back_to_a_number_roll(self) -> None:
        out = await ACTION_REGISTRY["random"].execute(_ctx({"choices": [], "min": 5, "max": 5}))
        assert out["value"] == 5 and out["choice"] is None


class TestLlmQuestion:
    """`llm_question` writes ONE complete multiple-choice question in the same shape a
    stored question record uses, so a create_record step can persist it field-for-field."""

    @pytest.mark.asyncio
    async def test_returns_every_question_field(self) -> None:
        captured: dict = {}

        async def fake_question(opts):
            captured.update(opts)
            return {
                "title": "Oxygen Fractions",
                "prompt": "The tank is 3/4 full of 200 L. How many liters?",
                "choice_a": "75",
                "choice_b": "150",
                "choice_c": "100",
                "choice_d": "175",
                "correct_choice": "B",
                "hint": "Quarter it, then take three.",
            }

        ctx = _ctx({"topic": "fractions on a ship", "audience": "{{inputs.band}} grade"}, inputs={"band": "3-5"})
        ctx.question = fake_question
        out = await ACTION_REGISTRY["llm_question"].execute(ctx)
        assert out["correct_choice"] == "B"
        assert out["choice_a"] == "75" and out["choice_d"] == "175"
        assert captured["audience"] == "3-5 grade"  # templates resolve before the call

    @pytest.mark.asyncio
    async def test_missing_topic_raises(self) -> None:
        ctx = _ctx({"audience": "2nd grade"})
        ctx.question = lambda opts: None
        with pytest.raises(ActionError):
            await ACTION_REGISTRY["llm_question"].execute(ctx)

    @pytest.mark.asyncio
    async def test_unavailable_capability_raises(self) -> None:
        with pytest.raises(ActionError):
            await ACTION_REGISTRY["llm_question"].execute(_ctx({"topic": "orbits"}))

    def test_dry_run_never_calls_the_model(self) -> None:
        out = ACTION_REGISTRY["llm_question"].simulate(_ctx({"topic": "orbits"}))
        assert out["prompt"] == "" and out["correct_choice"] == ""
