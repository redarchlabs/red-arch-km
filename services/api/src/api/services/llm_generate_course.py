"""Constrained LLM authoring of a full course "blueprint" (the ``generate_course`` tool).

The agent tool is the controller — it owns the topic, category, and audience, and what to
do with the result. This module is the subordinate authoring step: it turns those inputs
into ONE structured course blueprint — title/description, modules with slide decks, a
multiple-choice quiz, and a roleplay scenario — via a strict JSON schema, so authoring
always returns a parseable, bounded result. Persisting the blueprint as records is the
caller's job (see :class:`~api.services.course_generation.CourseGenerationService`),
keeping the LLM to authoring only.

Kept tiny and side-effect-free (given a client) so it is easy to test and mock.
"""

from __future__ import annotations

import json
from typing import Any

# Default authoring rules. The tool steers the content per request via the topic/
# category/audience in the user prompt so the course is grounded by the ask, not vibes.
DEFAULT_AUTHORING_RULES = (
    "You are an experienced instructional designer writing corporate training. Be accurate and "
    "concise. Slide bodies are short markdown — a few sentences or a tight bullet list, never a "
    "wall of text. Produce exactly the requested number of modules. Quiz options must all be "
    "plausible, and correct_answer must be copied verbatim from the options. The scenario is a "
    "realistic workplace roleplay the learner acts out with an AI counterpart. Return strictly "
    "the requested JSON."
)

# Bounds for the blueprint's integer fields (mirrored in the schema; re-clamped after
# parsing so a downstream record write never reads an out-of-range value).
_MINUTES_RANGE = (20, 120)
_THRESHOLD_RANGE = (60, 85)

_DIFFICULTIES = ("easy", "medium", "hard")


def _blueprint_schema() -> dict[str, Any]:
    """A strict JSON schema bounding the model to one complete course blueprint —
    modules with slides, an MCQ quiz, and a roleplay scenario — so authoring always
    yields a parseable, in-range result. ``modules`` deliberately has no min/max items
    (the requested count is runtime-variable, which a static schema can't express);
    the helper slices any excess after parsing."""
    slide = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string", "description": "Short slide title."},
            "body": {"type": "string", "description": "Short slide body in markdown."},
        },
        "required": ["title", "body"],
    }
    module = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string", "description": "Module title."},
            "slides": {"type": "array", "items": slide, "minItems": 3, "maxItems": 5},
        },
        "required": ["title", "slides"],
    }
    question = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "prompt": {"type": "string", "description": "The question the learner answers."},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 4,
                "maxItems": 4,
                "description": "Exactly four plausible answer options.",
            },
            "correct_answer": {
                "type": "string",
                "description": "The correct option, copied verbatim from options.",
            },
            "explanation": {"type": "string", "description": "Why the correct answer is right."},
        },
        "required": ["prompt", "options", "correct_answer", "explanation"],
    }
    quiz = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "passing_threshold": {
                "type": "integer",
                "minimum": _THRESHOLD_RANGE[0],
                "maximum": _THRESHOLD_RANGE[1],
                "description": "Percent score required to pass the quiz.",
            },
            "questions": {"type": "array", "items": question, "minItems": 4, "maxItems": 5},
        },
        "required": ["passing_threshold", "questions"],
    }
    scenario = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string", "description": "Scenario title."},
            "prompt": {"type": "string", "description": "The situation presented to the learner."},
            "persona": {"type": "string", "description": "Who the AI counterpart plays."},
            "rubric": {"type": "string", "description": "Criteria the grader scores against."},
            "learning_objective": {"type": "string", "description": "What the learner should demonstrate."},
            "skill_area": {"type": "string", "description": "The skill the scenario exercises."},
            "difficulty": {"type": "string", "enum": list(_DIFFICULTIES)},
            "pass_threshold": {
                "type": "integer",
                "minimum": _THRESHOLD_RANGE[0],
                "maximum": _THRESHOLD_RANGE[1],
                "description": "Score (of 100) required to pass the scenario.",
            },
        },
        "required": [
            "title",
            "prompt",
            "persona",
            "rubric",
            "learning_objective",
            "skill_area",
            "difficulty",
            "pass_threshold",
        ],
    }
    return {
        "name": "course_blueprint",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string", "description": "Course title."},
                "description": {"type": "string", "description": "One- or two-sentence course description."},
                "estimated_minutes": {
                    "type": "integer",
                    "minimum": _MINUTES_RANGE[0],
                    "maximum": _MINUTES_RANGE[1],
                    "description": "Estimated total minutes to complete the course.",
                },
                "modules": {"type": "array", "items": module},
                "quiz": quiz,
                "scenario": scenario,
            },
            "required": ["title", "description", "estimated_minutes", "modules", "quiz", "scenario"],
        },
    }


def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    """Coerce a model-returned integer to ``lo..hi`` (defensive: the strict schema
    should already bound it, but downstream record writes must never read a bad value)."""
    try:
        num = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, num))


def _clean_modules(raw: Any, num_modules: int) -> list[dict[str, Any]]:
    """Keep modules that have at least one well-formed slide, coercing titles/bodies to
    strings, and slice to at most ``num_modules`` (the schema can't bound a runtime-
    variable count, so the count is shaped here)."""
    modules: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        raw_slides = item.get("slides")
        slides = [
            {"title": str(s.get("title") or ""), "body": str(s.get("body") or "")}
            for s in (raw_slides if isinstance(raw_slides, list) else [])
            if isinstance(s, dict)
        ]
        if not slides:
            continue
        modules.append({"title": str(item.get("title") or ""), "slides": slides})
    return modules[:num_modules]


def _clean_questions(raw: Any) -> list[dict[str, Any]]:
    """Keep questions whose ``correct_answer`` appears verbatim in ``options`` (a
    question the grader could never mark correct is unusable) and coerce all text
    fields to strings."""
    questions: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        raw_options = item.get("options")
        options = [str(o) for o in (raw_options if isinstance(raw_options, list) else [])]
        correct = str(item.get("correct_answer") or "")
        if not options or correct not in options:
            continue
        questions.append(
            {
                "prompt": str(item.get("prompt") or ""),
                "options": options,
                "correct_answer": correct,
                "explanation": str(item.get("explanation") or ""),
            }
        )
    return questions


def _clean_scenario(raw: Any) -> dict[str, Any]:
    """Coerce the scenario's text fields to strings, its difficulty to a known level,
    and its pass threshold into range."""
    src = raw if isinstance(raw, dict) else {}
    difficulty = str(src.get("difficulty") or "").strip().lower()
    if difficulty not in _DIFFICULTIES:
        difficulty = "medium"
    return {
        "title": str(src.get("title") or ""),
        "prompt": str(src.get("prompt") or ""),
        "persona": str(src.get("persona") or ""),
        "rubric": str(src.get("rubric") or ""),
        "learning_objective": str(src.get("learning_objective") or ""),
        "skill_area": str(src.get("skill_area") or ""),
        "difficulty": difficulty,
        "pass_threshold": _clamp_int(src.get("pass_threshold"), *_THRESHOLD_RANGE, 70),
    }


async def generate_course_blueprint(
    client: Any,
    model: str,
    *,
    topic: str,
    category: str,
    audience: str = "all employees",
    num_modules: int = 3,
) -> dict[str, Any]:
    """Return a cleaned, structured course blueprint for ``topic``.

    ``client`` is an ``AsyncOpenAI`` instance (typed ``Any`` to keep this import-light
    and mockable). ``category`` and ``audience`` ground the content; ``num_modules`` is
    enforced by prompt plus post-parse slicing (a strict schema can't express a
    runtime-variable array length). Persisting the blueprint is the caller's; this only
    authors. Raises ``ValueError`` if the model returned no usable modules or quiz
    questions, and propagates a malformed model response — the caller surfaces that.
    """
    user = (
        "Author a corporate training course.\n\n"
        f"Topic: {topic}\n"
        f"Category: {category}\n"
        f"Audience: {audience}\n"
        f"Number of modules: exactly {num_modules}\n\n"
        "Each module needs 3-5 slides with short markdown bodies. The quiz needs 4-5 "
        "multiple-choice questions, each with exactly 4 options and its correct_answer copied "
        "verbatim from the options. Include one realistic workplace roleplay scenario."
    )

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": DEFAULT_AUTHORING_RULES},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_schema", "json_schema": _blueprint_schema()},
    )
    content = response.choices[0].message.content or "{}"
    parsed = json.loads(content)

    modules = _clean_modules(parsed.get("modules"), num_modules)
    if not modules:
        raise ValueError("course generation returned no usable modules")
    quiz = parsed.get("quiz") if isinstance(parsed.get("quiz"), dict) else {}
    questions = _clean_questions(quiz.get("questions"))
    if not questions:
        raise ValueError("course generation returned no usable quiz questions")

    return {
        "title": str(parsed.get("title") or ""),
        "description": str(parsed.get("description") or ""),
        "estimated_minutes": _clamp_int(parsed.get("estimated_minutes"), *_MINUTES_RANGE, 30),
        "modules": modules,
        "quiz": {
            "passing_threshold": _clamp_int(quiz.get("passing_threshold"), *_THRESHOLD_RANGE, 70),
            "questions": questions,
        },
        "scenario": _clean_scenario(parsed.get("scenario")),
    }
