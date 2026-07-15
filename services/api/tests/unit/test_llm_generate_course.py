"""Unit tests for the generate_course_blueprint helper — the constrained course-authoring LLM step.

Proves the design's headline claims: the model is bound to a strict JSON schema, the
runtime-variable module count is shaped by slicing after parsing, unusable questions
(correct_answer not among options) are dropped, integers are clamped into range, and a
blueprint with nothing usable raises instead of persisting garbage.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from api.services.llm_generate_course import generate_course_blueprint


class _FakeClient:
    """The minimal AsyncOpenAI surface the helper touches: ``chat.completions.create``."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.calls: list[dict[str, Any]] = []

        async def _create(**kwargs: Any) -> Any:
            self.calls.append(kwargs)
            message = SimpleNamespace(content=json.dumps(payload))
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))


def _module(i: int) -> dict[str, Any]:
    return {
        "title": f"Module {i}",
        "slides": [
            {"title": f"Slide {i}.{j}", "body": f"**Point {j}** of module {i}."} for j in range(1, 4)
        ],
    }


def _question(prompt: str = "What is phishing?", correct: str = "A scam email") -> dict[str, Any]:
    return {
        "prompt": prompt,
        "options": ["A scam email", "A firewall", "A password manager", "An antivirus"],
        "correct_answer": correct,
        "explanation": "Phishing is a fraudulent message that impersonates a trusted party.",
    }


def _blueprint(**overrides: Any) -> dict[str, Any]:
    bp: dict[str, Any] = {
        "title": "Phishing Defense Basics",
        "description": "Learn to spot and report phishing attempts.",
        "estimated_minutes": 45,
        "modules": [_module(i) for i in range(1, 4)],
        "quiz": {"passing_threshold": 70, "questions": [_question(), _question("Who to tell?")]},
        "scenario": {
            "title": "The Urgent Invoice",
            "prompt": "A vendor emails you demanding immediate payment.",
            "persona": "A pushy 'vendor' who is actually a scammer.",
            "rubric": "Learner verifies the sender and reports the email.",
            "learning_objective": "Verify before paying.",
            "skill_area": "email security",
            "difficulty": "medium",
            "pass_threshold": 70,
        },
    }
    bp.update(overrides)
    return bp


class TestGenerateCourseBlueprint:
    @pytest.mark.asyncio
    async def test_returns_cleaned_blueprint_and_binds_the_schema(self) -> None:
        client = _FakeClient(_blueprint())
        out = await generate_course_blueprint(
            client, "gpt-test", topic="phishing", category="security", num_modules=3
        )

        assert out["title"] == "Phishing Defense Basics"
        assert out["estimated_minutes"] == 45
        assert len(out["modules"]) == 3
        assert out["modules"][0]["slides"][0]["body"] == "**Point 1** of module 1."
        assert out["quiz"]["passing_threshold"] == 70
        assert len(out["quiz"]["questions"]) == 2
        assert out["scenario"]["difficulty"] == "medium"

        # The request was schema-bounded and prompt-targeted at the inputs.
        request = client.calls[0]
        assert request["response_format"]["type"] == "json_schema"
        assert request["response_format"]["json_schema"]["strict"] is True
        user = request["messages"][1]["content"]
        assert "phishing" in user and "security" in user and "exactly 3" in user

    @pytest.mark.asyncio
    async def test_slices_extra_modules_to_num_modules(self) -> None:
        client = _FakeClient(_blueprint(modules=[_module(i) for i in range(1, 6)]))
        out = await generate_course_blueprint(
            client, "gpt-test", topic="t", category="security", num_modules=3
        )
        assert len(out["modules"]) == 3
        assert [m["title"] for m in out["modules"]] == ["Module 1", "Module 2", "Module 3"]

    @pytest.mark.asyncio
    async def test_drops_question_whose_correct_answer_is_not_an_option(self) -> None:
        bad = _question(prompt="Broken?", correct="Not one of the options")
        client = _FakeClient(_blueprint(quiz={"passing_threshold": 70, "questions": [_question(), bad]}))
        out = await generate_course_blueprint(client, "gpt-test", topic="t", category="privacy")
        assert len(out["quiz"]["questions"]) == 1
        assert out["quiz"]["questions"][0]["prompt"] == "What is phishing?"

    @pytest.mark.asyncio
    async def test_clamps_out_of_range_integers(self) -> None:
        bp = _blueprint(estimated_minutes=999)
        bp["quiz"]["passing_threshold"] = 10
        bp["scenario"]["pass_threshold"] = 200
        out = await generate_course_blueprint(_FakeClient(bp), "gpt-test", topic="t", category="role")
        assert out["estimated_minutes"] == 120
        assert out["quiz"]["passing_threshold"] == 60
        assert out["scenario"]["pass_threshold"] == 85

    @pytest.mark.asyncio
    async def test_raises_when_no_usable_modules(self) -> None:
        client = _FakeClient(_blueprint(modules=[]))
        with pytest.raises(ValueError, match="modules"):
            await generate_course_blueprint(client, "gpt-test", topic="t", category="security")

    @pytest.mark.asyncio
    async def test_raises_when_no_usable_questions(self) -> None:
        bad = _question(correct="nope, not an option")
        client = _FakeClient(_blueprint(quiz={"passing_threshold": 70, "questions": [bad]}))
        with pytest.raises(ValueError, match="questions"):
            await generate_course_blueprint(client, "gpt-test", topic="t", category="security")
