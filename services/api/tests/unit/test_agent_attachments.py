"""Showing a model an image without putting it in the database.

Storage here is not reachable from a model provider, so an image has to travel as
a data URI inside the message. But messages are persisted — resume state lives in
``agent_runs.input`` and turns land in ``agent_run_steps`` — so the obvious
implementation writes a screenshot into a JSONB column, once per turn, once per
resume.

The rule these pin: **vision on arrival, text thereafter.**
"""

from __future__ import annotations

import base64
import json

import pytest
from api.services.agents import attachments as att

pytestmark = pytest.mark.unit


PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 2048


def _image(name: str = "shot.png", *, data: bytes = PNG, mime: str = "image/png") -> att.Attachment:
    return att.Attachment(document_id="doc-1", filename=name, mime=mime, data=data)


class TestWhatTheModelSees:
    def test_an_image_arrives_as_an_image(self) -> None:
        turn = att.build_user_turn("what is wrong here?", [_image()], vision=True)

        parts = turn["content"]
        assert [p["type"] for p in parts] == ["text", "image_url"]
        assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_the_bytes_survive_the_round_trip(self) -> None:
        turn = att.build_user_turn("", [_image()], vision=True)

        encoded = turn["content"][0]["image_url"]["url"].split(",", 1)[1]
        assert base64.b64decode(encoded) == PNG

    def test_a_model_without_vision_gets_a_working_message(self) -> None:
        """Not an error. An image_url part sent to a model that cannot see it fails
        the turn; text costs the picture and keeps the run."""
        turn = att.build_user_turn("look at this", [_image()], vision=False)

        assert isinstance(turn["content"], str)
        assert "shot.png" in turn["content"]
        assert "look at this" in turn["content"]

    def test_a_pdf_is_described_rather_than_shown(self) -> None:
        # The pipeline already extracts its text, which is the better representation.
        pdf = att.Attachment(document_id="d", filename="spec.pdf", mime="application/pdf", data=b"%PDF")

        turn = att.build_user_turn("read this", [pdf], vision=True)

        assert isinstance(turn["content"], str)
        assert "spec.pdf" in turn["content"]

    def test_extra_images_are_named_even_when_not_shown(self) -> None:
        """Silently dropping the fifth would leave the agent unable to tell it was
        ever attached."""
        shots = [_image(f"s{i}.png") for i in range(6)]

        turn = att.build_user_turn("", shots, vision=True)

        images = [p for p in turn["content"] if p["type"] == "image_url"]
        assert len(images) == att.MAX_IMAGES_PER_TURN
        trailing = [p for p in turn["content"] if p["type"] == "text"]
        assert "s5.png" in trailing[-1]["text"]

    def test_an_oversized_image_falls_back_to_text(self) -> None:
        huge = _image(data=b"x" * (att.MAX_IMAGE_BYTES + 1))

        turn = att.build_user_turn("", [huge], vision=True)

        assert isinstance(turn["content"], str)


class TestWhatGetsStored:
    def test_the_persisted_turn_carries_no_image_bytes(self) -> None:
        # The whole point. Revert the flattening and this row grows by megabytes
        # every time the run parks.
        attachment = _image()
        turn = att.build_user_turn("what is wrong here?", [attachment], vision=True)

        stored = att.persistable(turn, [attachment])

        blob = json.dumps(stored)
        assert "base64" not in blob
        assert len(blob) < 500
        assert "shot.png" in stored["content"]

    def test_it_still_says_what_was_there(self) -> None:
        """A later turn reading the transcript must be able to tell an image was
        attached, and which document it became."""
        attachment = _image()
        stored = att.persistable(att.build_user_turn("hi", [attachment], vision=True), [attachment])

        assert "doc-1" in stored["content"]
        assert "hi" in stored["content"]

    def test_an_ordinary_message_is_untouched(self) -> None:
        plain = {"role": "user", "content": "no attachments here"}

        assert att.persistable(plain, []) == plain


class TestFlattening:
    def test_multimodal_content_reads_as_text(self) -> None:
        """Anywhere content meets an f-string — the summary, the diary — a list
        would render as a Python repr inside prose the model later reads."""
        turn = att.build_user_turn("before", [_image()], vision=True)

        assert att.flatten_content(turn["content"]) == "before [image]"

    def test_a_string_passes_through(self) -> None:
        assert att.flatten_content("plain") == "plain"

    def test_nothing_is_empty_not_none(self) -> None:
        assert att.flatten_content(None) == ""


class TestWhichModelsCanSee:
    def test_the_hosted_models_can(self) -> None:
        from api.services.agents.llm.catalog import model_supports_vision

        assert model_supports_vision("gpt-5-mini")
        assert model_supports_vision("anthropic/claude-sonnet-5")

    def test_an_unknown_model_is_assumed_blind(self) -> None:
        """A locally served model is not in the catalog. Guessing yes sends an
        image_url part to something that errors on it mid-run; guessing no costs
        a picture."""
        from api.services.agents.llm.catalog import model_supports_vision

        assert not model_supports_vision("qwen3-30b")
        assert not model_supports_vision("openai/qwen3-30b")
