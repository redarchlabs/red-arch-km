"""Unit tests for the constrained question-authoring helper (`llm_question`)."""

from __future__ import annotations

import json

import pytest
from api.services.llm_question import generate_question


class _FakeClient:
    """Minimal AsyncOpenAI stand-in capturing the request and returning canned JSON."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.kwargs: dict = {}
        self.chat = self  # client.chat.completions.create(...)
        self.completions = self

    async def create(self, **kwargs):
        self.kwargs = kwargs
        content = json.dumps(self.payload)
        return type(
            "R",
            (),
            {"choices": [type("C", (), {"message": type("M", (), {"content": content})()})()]},
        )()


_GOOD = {
    "title": "Fuel Cells",
    "prompt": "3 blue cells and 2 red cells. How many in all?",
    "choice_a": "4",
    "choice_b": "5",
    "choice_c": "6",
    "choice_d": "3",
    "correct_choice": "B",
    "hint": "Count them together.",
}


@pytest.mark.asyncio
async def test_returns_a_complete_storable_question() -> None:
    client = _FakeClient(_GOOD)
    out = await generate_question(client, "gpt-4.1-mini", topic="counting", audience="a 1st grader")
    assert out == _GOOD
    # The strict schema is what makes the result storable field-for-field.
    assert client.kwargs["response_format"]["type"] == "json_schema"
    assert client.kwargs["response_format"]["json_schema"]["strict"] is True


@pytest.mark.asyncio
async def test_audience_and_style_reach_the_prompt() -> None:
    client = _FakeClient(_GOOD)
    await generate_question(client, "m", topic="orbits", audience="a 9th-grade physics class", style="ship's computer")
    user = client.kwargs["messages"][1]["content"]
    assert "a 9th-grade physics class" in user and "ship's computer" in user


@pytest.mark.asyncio
async def test_bad_answer_key_falls_back_to_a_usable_letter() -> None:
    # An unanswerable question is worse than a wrong-but-answerable one: grading
    # compares the crew's letter against this value.
    client = _FakeClient({**_GOOD, "correct_choice": "banana"})
    out = await generate_question(client, "m", topic="x")
    assert out["correct_choice"] == "A"


@pytest.mark.asyncio
async def test_lowercase_answer_key_is_normalized() -> None:
    client = _FakeClient({**_GOOD, "correct_choice": "c"})
    out = await generate_question(client, "m", topic="x")
    assert out["correct_choice"] == "C"


@pytest.mark.asyncio
async def test_missing_fields_become_empty_strings_not_none() -> None:
    client = _FakeClient({"prompt": "just a prompt"})
    out = await generate_question(client, "m", topic="x")
    assert out["title"] == "" and out["choice_a"] == ""
    assert all(isinstance(v, str) for v in out.values())
