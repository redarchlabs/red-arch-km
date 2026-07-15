"""Unit tests for the generated-course play-view builders — the quiz + scenario views
that make a generated course playable. Pure config construction; each result is also
validated as a real ``FormConfig`` so a generated view can't ship malformed."""

from __future__ import annotations

import uuid

from api.schemas.form import FormConfig
from api.services import lms_play_views


def _walk(elements):
    for e in elements:
        yield e
        for key in ("elements", "panes", "tabs", "columns"):
            for child in e.get(key, []) or []:
                if isinstance(child, dict) and child.get("elements"):
                    yield from _walk(child["elements"])
        if e.get("elements"):
            yield from _walk(e["elements"])


def test_play_view_slug_sanitizes_code():
    assert lms_play_views.play_view_slug("quiz", "COM-1A2B3C4D") == "quiz_gen_com_1a2b3c4d"
    assert lms_play_views.play_view_slug("scenario", "SEC-9f9f9f9f") == "scenario_gen_sec_9f9f9f9f"


class TestQuizView:
    def _cfg(self, n=3):
        questions = [
            {"prompt": f"Q{i}?", "options": ["A", "B", "C"], "correct_answer": "A"} for i in range(1, n + 1)
        ]
        return lms_play_views.build_quiz_view_config(
            title="Fire Safety",
            questions=questions,
            assessment_id=str(uuid.uuid4()),
            quiz_workflow_id=str(uuid.uuid4()),
            passing_threshold=70,
        )

    def test_one_select_per_question_with_positional_inputs(self):
        cfg = self._cfg(3)
        els = cfg["elements"]
        inputs = [e for e in els if e.get("type") == "input"]
        assert [i["key"] for i in inputs] == ["a1", "a2", "a3"]
        assert all(i["control"] == "select" for i in inputs)
        assert all(len(i["options"]) == 3 for i in inputs)

    def test_submit_button_passes_answers_assessment_and_learner_email(self):
        cfg = self._cfg(2)
        btn = next(e for e in cfg["elements"] if e.get("type") == "button")
        ins = btn["action"]["inputs"]
        assert ins["a1"] == {"var": "a1"} and ins["a2"] == {"var": "a2"}
        assert isinstance(ins["assessment_id"], str)
        assert ins["learner_email"] == {"var": "email"}
        assert btn["action"]["kind"] == "run_workflow"

    def test_hidden_email_field_and_result_board(self):
        cfg = self._cfg(1)
        email = next(e for e in cfg["elements"] if e.get("type") == "field")
        assert email["slug"] == "email" and email["visible_when"] is False
        rl = next(e for e in cfg["elements"] if e.get("type") == "record_list")
        assert rl["entity"] == "assessment_attempt"
        assert {"field": "learner", "op": "eq", "value": "@me"} in rl["filters"]

    def test_config_is_a_valid_formconfig(self):
        FormConfig.model_validate(self._cfg(4))  # no raise → well-formed element tree


class TestScenarioView:
    def _cfg(self):
        return lms_play_views.build_scenario_view_config(
            title="Angry customer",
            prompt="You are a support agent…",
            scenario_id=str(uuid.uuid4()),
            course_id=str(uuid.uuid4()),
            scenario_workflow_id=str(uuid.uuid4()),
        )

    def test_response_textarea_and_grade_button(self):
        cfg = self._cfg()
        inp = next(e for e in cfg["elements"] if e.get("type") == "input")
        assert inp["key"] == "response" and inp["control"] == "textarea"
        btn = next(e for e in cfg["elements"] if e.get("type") == "button")
        ins = btn["action"]["inputs"]
        assert ins["response"] == {"var": "response"}
        assert isinstance(ins["scenario_id"], str)
        assert ins["learner_email"] == {"var": "email"}

    def test_result_and_course_scoped_certificate_boards(self):
        cfg = self._cfg()
        lists = [e for e in cfg["elements"] if e.get("type") == "record_list"]
        entities = {rl["entity"] for rl in lists}
        assert entities == {"simulation_attempt", "certification"}
        cert = next(rl for rl in lists if rl["entity"] == "certification")
        assert any(f["field"] == "course" for f in cert["filters"])

    def test_config_is_a_valid_formconfig(self):
        FormConfig.model_validate(self._cfg())
