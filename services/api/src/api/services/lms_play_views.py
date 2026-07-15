"""Build the learner-facing quiz + scenario PLAY views for a course.

A generated course creates the record graph (course/modules/assessment/questions/
scenario) but, like the hand-built courses, needs two learner-bound views to be
*played*: a multiple-choice quiz (each question rendered as a ``select``, submitting to
the org's "Quiz: Grade" workflow) and a roleplay scenario (a response textarea
submitting to "Scenario: Grade & Certify"). Both bind to the ``learner`` entity and are
opened with ``record_id=me`` so ``{var: email}`` resolves the caller — the exact shape
the hand-built ``quiz_gate_*`` / ``scenario_assess_*`` views use, so a generated view
reuses the SAME grading workflows (already generic: parameterised by the
``assessment_id`` / ``scenario_id`` the view passes as run inputs).

Pure config builders (no DB) so they're unit-testable; :mod:`course_generation`
resolves the ids and persists the views.
"""

from __future__ import annotations

import re
from typing import Any

# The result boards poll briefly so a learner sees their graded score/cert land without
# a manual refresh (the grading workflow writes the record a moment after submit).
_RESULT_POLL_MS = 2500


def play_view_slug(kind: str, code: str) -> str:
    """A stable, unique play-view slug for a course, e.g. ``quiz_gen_com_1a2b3c4d``.
    ``code`` is the course code (``CAT-<hex>``); sanitised to ``[a-z0-9_]``."""
    safe = re.sub(r"[^a-z0-9_]", "_", code.lower())
    return f"{kind}_gen_{safe}"


def _email_field() -> dict[str, Any]:
    # The learner's own email, pulled into scope for {var: email} but never shown
    # (visible_when false) — this is what makes the view learner-bound via record_id=me.
    return {"type": "field", "slug": "email", "read_only": True, "visible_when": False}


def build_quiz_view_config(
    *,
    title: str,
    questions: list[dict[str, Any]],
    assessment_id: str,
    quiz_workflow_id: str,
    passing_threshold: int | None,
) -> dict[str, Any]:
    """A learner-bound MCQ quiz: one ``select`` per question (positional inputs
    ``a1..aN`` — the order the grader reads), a submit button running the Quiz: Grade
    workflow with the answers + ``assessment_id`` + ``learner_email``, and a live result
    board of the learner's own attempt for this assessment."""
    intro = (
        f"Choose the best answer for each question, then submit. You need "
        f"{passing_threshold}% or higher to pass."
        if passing_threshold
        else "Choose the best answer for each question, then submit."
    )
    elements: list[dict[str, Any]] = [
        {"type": "label", "variant": "heading", "text": f"Quiz: {title}"},
        {"type": "label", "variant": "paragraph", "text": intro},
        _email_field(),
    ]
    inputs: dict[str, Any] = {}
    for i, q in enumerate(questions, start=1):
        key = f"a{i}"
        options = [{"value": str(o), "label": None} for o in (q.get("options") or [])]
        elements.append(
            {"type": "label", "variant": "paragraph", "text": f"{i}. {q.get('prompt') or ''}"}
        )
        elements.append(
            {"type": "input", "key": key, "control": "select", "label": "Your answer", "options": options}
        )
        inputs[key] = {"var": key}
    inputs["assessment_id"] = assessment_id
    inputs["learner_email"] = {"var": "email"}
    elements.append(
        {
            "type": "button",
            "label": "Submit quiz",
            "style": "primary",
            "action": {
                "kind": "run_workflow",
                "workflow_id": quiz_workflow_id,
                "inputs": inputs,
                "success_message": "Quiz submitted — your result appears below in a moment.",
            },
        }
    )
    elements.append({"type": "label", "variant": "subheading", "text": "Your result"})
    elements.append(
        {
            "type": "record_list",
            "entity": "assessment_attempt",
            "fields": ["passed", "score"],
            "filters": [
                {"field": "learner", "op": "eq", "value": "@me"},
                {"field": "assessment", "op": "eq", "value": assessment_id},
            ],
            "poll_ms": _RESULT_POLL_MS,
        }
    )
    return {"version": 2, "elements": elements}


def build_scenario_view_config(
    *,
    title: str,
    prompt: str,
    scenario_id: str,
    course_id: str,
    scenario_workflow_id: str,
) -> dict[str, Any]:
    """A learner-bound roleplay scenario: the scenario prompt, a response textarea, a
    submit button running the Scenario: Grade & Certify workflow with the response +
    ``scenario_id`` + ``learner_email``, then live result + certificate boards (the cert
    board scoped to this course)."""
    elements: list[dict[str, Any]] = [
        {"type": "label", "variant": "heading", "text": f"Scenario: {title}"},
        {
            "type": "label",
            "variant": "paragraph",
            "text": (
                "Read the scenario, then describe exactly how you would handle it. Your "
                "response is graded, and if you pass (having also passed the quiz) you're "
                "certified."
            ),
        },
        _email_field(),
        {"type": "panel", "elements": [{"type": "label", "variant": "paragraph", "text": prompt}]},
        {"type": "input", "key": "response", "control": "textarea", "label": "Your response"},
        {
            "type": "button",
            "label": "Submit for grading",
            "style": "primary",
            "action": {
                "kind": "run_workflow",
                "workflow_id": scenario_workflow_id,
                "inputs": {
                    "response": {"var": "response"},
                    "scenario_id": scenario_id,
                    "learner_email": {"var": "email"},
                },
                "success_message": "Submitted — your result appears below in a moment.",
            },
        },
        {"type": "label", "variant": "subheading", "text": "Your result"},
        {
            "type": "record_list",
            "entity": "simulation_attempt",
            "fields": ["passed", "score", "feedback"],
            "filters": [{"field": "learner", "op": "eq", "value": "@me"}],
            "poll_ms": _RESULT_POLL_MS,
        },
        {"type": "label", "variant": "subheading", "text": "Your certificate"},
        {
            "type": "record_list",
            "entity": "certification",
            "fields": ["certificate_no", "issued_date", "status"],
            "filters": [
                {"field": "learner", "op": "eq", "value": "@me"},
                {"field": "course", "op": "eq", "value": course_id},
            ],
            "poll_ms": _RESULT_POLL_MS,
        },
    ]
    return {"version": 2, "elements": elements}
