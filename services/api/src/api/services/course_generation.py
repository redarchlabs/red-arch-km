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

from api.repositories.custom_entity import EntityDefinitionRepository, EntityFieldRepository
from api.repositories.workflow import WorkflowRepository
from api.schemas.form import FormConfig
from api.schemas.view import ViewCreate
from api.services import lms_play_views
from api.services.entity_records_helpers import build_record_repo
from api.services.view_service import ViewService

# Fallback per-module minutes when the blueprint carries no course estimate to split.
_DEFAULT_MODULE_MINUTES = 10

# The org workflows a generated course's play views submit to. Already generic
# (parameterised by the assessment_id / scenario_id the view passes), so a generated
# view reuses them exactly as the hand-built views do.
_QUIZ_WORKFLOW_NAME = "Quiz: Grade"
_SCENARIO_WORKFLOW_NAME = "Scenario: Grade & Certify"
# Course fields that point at the play views, letting the generic catalog/player route
# to a course's own quiz/scenario without any per-course view edits.
_COURSE_VIEW_SLUG_FIELDS = ("quiz_view_slug", "scenario_view_slug")


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

    async def _update(self, slug: str, record_id: str, values: dict[str, Any]) -> None:
        """Patch fields on an existing record (privileged, like ``_create``)."""
        repo, _definition = await build_record_repo(self._session, self._org_id, slug, privileged=True)
        await repo.update(uuid.UUID(record_id), values)

    async def _resolve_workflow_id(self, name: str) -> str | None:
        """Resolve a workflow by name in this org (the play views submit to it)."""
        for wf in await WorkflowRepository(self._session, self._org_id).list_all():
            if wf.name == name:
                return str(wf.id)
        return None

    async def _course_has_view_slug_fields(self) -> bool:
        definition = await EntityDefinitionRepository(self._session, self._org_id).get_by_slug("course")
        if definition is None:
            return False
        fields = await EntityFieldRepository(self._session, self._org_id).list_for_definition(definition.id)
        have = {f.slug for f in fields}
        return all(slug in have for slug in _COURSE_VIEW_SLUG_FIELDS)

    async def create_play_views(
        self,
        *,
        course_id: str,
        code: str,
        title: str,
        assessment_id: str,
        questions: list[dict[str, Any]],
        passing_threshold: int | None,
        scenario_id: str,
        scenario_title: str,
        scenario_prompt: str,
    ) -> dict[str, str | None]:
        """Create the learner-bound quiz + scenario play views for a generated course
        and link the course to them (so the generic catalog/player can route to it).

        Best-effort: if the org is missing the grading workflows or the ``learner``
        entity, the course's records still stand (browsable) — it just isn't playable
        yet, and the returned slugs are ``None``."""
        quiz_wf = await self._resolve_workflow_id(_QUIZ_WORKFLOW_NAME)
        scenario_wf = await self._resolve_workflow_id(_SCENARIO_WORKFLOW_NAME)
        learner = await EntityDefinitionRepository(self._session, self._org_id).get_by_slug("learner")
        if not (quiz_wf and scenario_wf and learner):
            return {"quiz_view_slug": None, "scenario_view_slug": None}

        views = ViewService(self._session, self._org_id)
        quiz_slug = lms_play_views.play_view_slug("quiz", code)
        scenario_slug = lms_play_views.play_view_slug("scenario", code)
        await views.create_view(
            ViewCreate(
                name=f"Quiz: {title}",
                slug=quiz_slug,
                entity_definition_id=learner.id,
                config=FormConfig.model_validate(
                    lms_play_views.build_quiz_view_config(
                        title=title,
                        questions=questions,
                        assessment_id=assessment_id,
                        quiz_workflow_id=quiz_wf,
                        passing_threshold=passing_threshold,
                    )
                ),
            )
        )
        await views.create_view(
            ViewCreate(
                name=f"Scenario: {scenario_title}",
                slug=scenario_slug,
                entity_definition_id=learner.id,
                config=FormConfig.model_validate(
                    lms_play_views.build_scenario_view_config(
                        title=scenario_title,
                        prompt=scenario_prompt,
                        scenario_id=scenario_id,
                        course_id=course_id,
                        scenario_workflow_id=scenario_wf,
                    )
                ),
            )
        )
        # Link the course to its play views so the generic catalog/player route to it.
        if await self._course_has_view_slug_fields():
            await self._update(
                "course",
                course_id,
                {"quiz_view_slug": quiz_slug, "scenario_view_slug": scenario_slug},
            )
        return {"quiz_view_slug": quiz_slug, "scenario_view_slug": scenario_slug}

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

        # Create the learner-bound quiz + scenario play views and link the course to
        # them, so the generated course is playable through the generic catalog/player.
        play = await self.create_play_views(
            course_id=course_id,
            code=code,
            title=title,
            assessment_id=assessment_id,
            questions=quiz.get("questions") or [],
            passing_threshold=quiz.get("passing_threshold"),
            scenario_id=str(scenario["id"]),
            scenario_title=str(scenario_bp.get("title") or title),
            scenario_prompt=str(scenario_bp.get("prompt") or ""),
        )

        return {
            "course_id": course_id,
            "code": code,
            "module_ids": module_ids,
            "assessment_id": assessment_id,
            "question_ids": question_ids,
            "scenario_id": str(scenario["id"]),
            "quiz_view_slug": play["quiz_view_slug"],
            "scenario_view_slug": play["scenario_view_slug"],
            "title": title,
        }
