"""Persist a generated course blueprint as linked LMS records.

The subordinate persistence step of the ``generate_course`` agent tool: it takes the
cleaned blueprint from :func:`~api.services.llm_generate_course.generate_course_blueprint`
and creates the linked records — a course, its modules (slide decks), one quiz assessment
with its questions, and one roleplay scenario — resolving every entity by SLUG through
:func:`~api.services.entity_records_helpers.build_record_repo` so record writes go through
the same repository path (outbox included) as the UI. The session is NOT committed here;
the caller owns the transaction.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.services.entity_records_helpers import build_record_repo

# Fallback per-module minutes when the blueprint carries no course estimate to split.
_DEFAULT_MODULE_MINUTES = 10


class CourseGenerationService:
    """Create the record graph for one generated course inside the caller's session."""

    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def _create(self, slug: str, values: dict[str, Any]) -> dict[str, Any]:
        """Create one record in the entity ``slug`` (resolved per call — the five LMS
        entities are looked up by slug, never by hardcoded id) and return it, raising
        if the repository yielded no record/id so a half-linked graph never persists
        silently.

        Repos are built ``privileged`` because course authoring is a trusted server-side
        admin action (the ``generate_course`` agent tool is admin-gated): it must write
        the quiz answer key (``question.correct_answer`` is a ``server_only`` field a
        non-privileged write would silently drop) and any ``workflow_only`` LMS entity
        without tripping the record tamper-proofing policy."""
        repo, _definition = await build_record_repo(self._session, self._org_id, slug, privileged=True)
        record = await repo.create(values)
        if not record or not record.get("id"):
            raise RuntimeError(f"record create for entity '{slug}' returned no id")
        return record

    async def create_from_blueprint(self, blueprint: dict[str, Any], category: str) -> dict[str, Any]:
        """Create the linked records for a generated course and return their ids.

        ``category`` is the tool input (privacy|security|role|compliance|onboarding) —
        the blueprint itself doesn't carry it. The course ``code`` is unique by
        construction: a category prefix plus an 8-char uuid4 hex slice.
        """
        title = str(blueprint.get("title") or "")
        estimated_minutes = int(blueprint.get("estimated_minutes") or 0)
        code = f"{category[:3].upper()}-{uuid.uuid4().hex[:8].upper()}"

        course = await self._create(
            "course",
            {
                "title": title,
                "description": str(blueprint.get("description") or ""),
                "category": category,
                "estimated_minutes": estimated_minutes,
                "code": code,
                "status": "published",
            },
        )
        course_id = str(course["id"])

        modules: list[dict[str, Any]] = blueprint.get("modules") or []
        module_minutes = (
            max(1, estimated_minutes // len(modules)) if estimated_minutes > 0 else _DEFAULT_MODULE_MINUTES
        )
        module_ids: list[str] = []
        for i, module in enumerate(modules):
            created = await self._create(
                "module",
                {
                    "title": str(module.get("title") or ""),
                    "slides": module.get("slides") or [],
                    "sort_order": i + 1,
                    "content_type": "reading",
                    "estimated_minutes": module_minutes,
                    "course": course_id,
                },
            )
            module_ids.append(str(created["id"]))

        quiz: dict[str, Any] = blueprint.get("quiz") or {}
        assessment = await self._create(
            "assessment",
            {
                "title": f"{title} — Quiz",
                "passing_threshold": quiz.get("passing_threshold"),
                "course": course_id,
            },
        )
        assessment_id = str(assessment["id"])

        question_ids: list[str] = []
        for i, question in enumerate(quiz.get("questions") or []):
            created = await self._create(
                "question",
                {
                    "prompt": str(question.get("prompt") or ""),
                    "type": "mcq",
                    "options": question.get("options") or [],
                    "correct_answer": str(question.get("correct_answer") or ""),
                    "explanation": str(question.get("explanation") or ""),
                    "points": 1,
                    "sort_order": i + 1,
                    "assessment": assessment_id,
                },
            )
            question_ids.append(str(created["id"]))

        scenario_bp: dict[str, Any] = blueprint.get("scenario") or {}
        scenario = await self._create(
            "scenario",
            {
                "title": str(scenario_bp.get("title") or ""),
                "prompt": str(scenario_bp.get("prompt") or ""),
                "persona": str(scenario_bp.get("persona") or ""),
                "rubric": str(scenario_bp.get("rubric") or ""),
                "learning_objective": str(scenario_bp.get("learning_objective") or ""),
                "skill_area": str(scenario_bp.get("skill_area") or ""),
                "difficulty": str(scenario_bp.get("difficulty") or "medium"),
                "pass_threshold": scenario_bp.get("pass_threshold"),
                "mode": "roleplay",
                "max_score": 100,
                "category": category,
                "course": course_id,
            },
        )

        return {
            "course_id": course_id,
            "code": code,
            "module_ids": module_ids,
            "assessment_id": assessment_id,
            "question_ids": question_ids,
            "scenario_id": str(scenario["id"]),
            "title": title,
        }
