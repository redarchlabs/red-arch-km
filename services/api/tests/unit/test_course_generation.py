"""Unit tests for CourseGenerationService — persisting a course blueprint as linked records.

Uses one fake repo PER entity slug (course/module/assessment/question/scenario) in place of
``build_record_repo`` so every write is asserted per entity, without a database: the course
gets a unique category-prefixed code, children carry the right FK + sort_order, and the
returned ids dict is complete.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from api.services import course_generation
from api.services.course_generation import CourseGenerationService

_SLUGS = ("course", "module", "assessment", "question", "scenario")


class _FakeRepo:
    """Records every ``create(values)`` call and hands back a fresh uuid id."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.ids: list[str] = []

    async def create(self, values: dict[str, Any]) -> dict[str, Any]:
        record_id = str(uuid.uuid4())
        self.created.append(values)
        self.ids.append(record_id)
        return {"id": record_id, **values}


@pytest.fixture()
def repos(monkeypatch: pytest.MonkeyPatch) -> dict[str, _FakeRepo]:
    fakes = {slug: _FakeRepo() for slug in _SLUGS}

    async def fake_build_record_repo(
        session: Any, org_id: Any, slug: str, *, privileged: bool = False
    ) -> tuple[_FakeRepo, None]:
        # Course authoring must write the server-only answer key + any workflow-only
        # LMS entity, so it MUST build privileged repos (else the policy silently drops
        # correct_answer / 403s the write). Assert that contract holds.
        assert privileged is True, f"course generation must build a privileged repo for {slug!r}"
        return fakes[slug], None

    monkeypatch.setattr(course_generation, "build_record_repo", fake_build_record_repo)
    return fakes


def _blueprint() -> dict[str, Any]:
    return {
        "title": "Phishing Defense Basics",
        "description": "Learn to spot and report phishing attempts.",
        "estimated_minutes": 45,
        "modules": [
            {"title": "Spotting the Hook", "slides": [{"title": "s1", "body": "b1"}, {"title": "s2", "body": "b2"}]},
            {"title": "Reporting It", "slides": [{"title": "s3", "body": "b3"}]},
        ],
        "quiz": {
            "passing_threshold": 70,
            "questions": [
                {
                    "prompt": "What is phishing?",
                    "options": ["A scam email", "A firewall", "A password manager", "An antivirus"],
                    "correct_answer": "A scam email",
                    "explanation": "It impersonates a trusted party.",
                },
                {
                    "prompt": "Who do you tell?",
                    "options": ["Nobody", "Security team", "The sender", "A friend"],
                    "correct_answer": "Security team",
                    "explanation": "Report suspected phishing to security.",
                },
            ],
        },
        "scenario": {
            "title": "The Urgent Invoice",
            "prompt": "A vendor emails demanding immediate payment.",
            "persona": "A pushy scammer posing as a vendor.",
            "rubric": "Learner verifies the sender and reports the email.",
            "learning_objective": "Verify before paying.",
            "skill_area": "email security",
            "difficulty": "medium",
            "pass_threshold": 70,
        },
    }


def _service() -> CourseGenerationService:
    return CourseGenerationService(object(), uuid.uuid4())  # type: ignore[arg-type]


class TestCreateFromBlueprint:
    @pytest.mark.asyncio
    async def test_creates_course_published_with_unique_coded_values(self, repos: dict[str, _FakeRepo]) -> None:
        result = await _service().create_from_blueprint(_blueprint(), "security")

        assert len(repos["course"].created) == 1
        course = repos["course"].created[0]
        assert course["title"] == "Phishing Defense Basics"
        assert course["description"] == "Learn to spot and report phishing attempts."
        assert course["category"] == "security"
        assert course["estimated_minutes"] == 45
        assert course["status"] == "published"
        assert re.fullmatch(r"(PRI|SEC|ROL|COM|ONB)-[0-9A-F]{8}", course["code"])
        assert result["code"] == course["code"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("category", "prefix"),
        [
            ("privacy", "PRI"),
            ("security", "SEC"),
            ("role", "ROL"),
            ("compliance", "COM"),
            ("onboarding", "ONB"),
        ],
    )
    async def test_code_prefix_follows_category(
        self, repos: dict[str, _FakeRepo], category: str, prefix: str
    ) -> None:
        result = await _service().create_from_blueprint(_blueprint(), category)
        assert result["code"].startswith(f"{prefix}-")

    @pytest.mark.asyncio
    async def test_creates_modules_sorted_and_linked_with_slides(self, repos: dict[str, _FakeRepo]) -> None:
        result = await _service().create_from_blueprint(_blueprint(), "security")

        course_id = repos["course"].ids[0]
        assert result["course_id"] == course_id
        modules = repos["module"].created
        assert len(modules) == 2
        assert [m["sort_order"] for m in modules] == [1, 2]
        assert all(m["course"] == course_id for m in modules)
        assert all(m["content_type"] == "reading" for m in modules)
        # Slides json passes through untouched.
        assert modules[0]["slides"] == [{"title": "s1", "body": "b1"}, {"title": "s2", "body": "b2"}]
        assert result["module_ids"] == repos["module"].ids

    @pytest.mark.asyncio
    async def test_creates_assessment_and_questions_linked(self, repos: dict[str, _FakeRepo]) -> None:
        result = await _service().create_from_blueprint(_blueprint(), "security")

        assert len(repos["assessment"].created) == 1
        assessment = repos["assessment"].created[0]
        assert assessment["title"] == "Phishing Defense Basics — Quiz"
        assert assessment["passing_threshold"] == 70
        assert assessment["course"] == repos["course"].ids[0]
        assert result["assessment_id"] == repos["assessment"].ids[0]

        questions = repos["question"].created
        assert len(questions) == 2
        assert [q["sort_order"] for q in questions] == [1, 2]
        assert all(q["assessment"] == repos["assessment"].ids[0] for q in questions)
        assert all(q["type"] == "mcq" for q in questions)
        assert all(q["points"] == 1 for q in questions)
        assert questions[1]["correct_answer"] == "Security team"
        assert result["question_ids"] == repos["question"].ids

    @pytest.mark.asyncio
    async def test_creates_scenario_linked_with_mode_and_category(self, repos: dict[str, _FakeRepo]) -> None:
        result = await _service().create_from_blueprint(_blueprint(), "privacy")

        assert len(repos["scenario"].created) == 1
        scenario = repos["scenario"].created[0]
        assert scenario["course"] == repos["course"].ids[0]
        assert scenario["category"] == "privacy"
        assert scenario["mode"] == "roleplay"
        assert scenario["max_score"] == 100
        assert scenario["difficulty"] == "medium"
        assert scenario["pass_threshold"] == 70
        assert result["scenario_id"] == repos["scenario"].ids[0]

    @pytest.mark.asyncio
    async def test_returns_all_id_keys(self, repos: dict[str, _FakeRepo]) -> None:
        result = await _service().create_from_blueprint(_blueprint(), "compliance")
        assert set(result) == {
            "course_id",
            "code",
            "module_ids",
            "assessment_id",
            "question_ids",
            "scenario_id",
            "title",
        }
        assert result["title"] == "Phishing Defense Basics"

    @pytest.mark.asyncio
    async def test_raises_when_create_returns_nothing(self, repos: dict[str, _FakeRepo]) -> None:
        async def _create_none(values: dict[str, Any]) -> None:
            return None

        repos["course"].create = _create_none  # type: ignore[method-assign, assignment]
        with pytest.raises(RuntimeError, match="course"):
            await _service().create_from_blueprint(_blueprint(), "security")
