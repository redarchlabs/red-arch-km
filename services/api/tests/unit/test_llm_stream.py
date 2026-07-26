"""Streaming a single field out of a structured (JSON-schema) LLM response.

``llm_respond``/``llm_decide`` return strict JSON, so raw token deltas would show
a viewer ``{"reply":"Hel``. These tests pin the extraction of the ONE
user-visible field from a partial document, and that the assembled raw content is
still returned unchanged so existing parsing is unaffected.
"""

from __future__ import annotations

import pytest

from api.services.llm_stream import partial_string_field, stream_json_content


class TestPartialStringField:
    def test_reads_a_complete_value(self) -> None:
        assert partial_string_field('{"reply": "Hello there", "done": false}', "reply") == "Hello there"

    def test_reads_a_value_that_is_still_being_written(self) -> None:
        assert partial_string_field('{"reply": "Hello th', "reply") == "Hello th"

    def test_returns_empty_before_the_field_appears(self) -> None:
        assert partial_string_field('{"co', "reply") == ""
        assert partial_string_field('{"reply"', "reply") == ""
        assert partial_string_field('{"reply":', "reply") == ""
        assert partial_string_field('{"reply": ', "reply") == ""
        assert partial_string_field('{"reply": "', "reply") == ""

    def test_decodes_escapes(self) -> None:
        assert partial_string_field(r'{"reply": "line\nnext"', "reply") == "line\nnext"
        assert partial_string_field(r'{"reply": "say \"hi\""', "reply") == 'say "hi"'
        assert partial_string_field(r'{"reply": "back\\slash"', "reply") == "back\\slash"
        assert partial_string_field(r'{"reply": "été"', "reply") == "été"

    def test_waits_for_an_incomplete_escape(self) -> None:
        """A lone trailing backslash is half a character — wait for the rest."""
        assert partial_string_field('{"reply": "a' + "\\", "reply") == "a"
        assert partial_string_field(r'{"reply": "a\u00', "reply") == "a"

    def test_a_completed_escape_resolves(self) -> None:
        assert partial_string_field(r'{"reply": "a\\', "reply") == "a\\"  # \\ -> one backslash
        assert partial_string_field(r'{"reply": "ab', "reply") == "ab"

    def test_stops_at_the_closing_quote(self) -> None:
        value = partial_string_field('{"reply": "done", "coach": "later"}', "reply")
        assert value == "done"

    def test_ignores_a_different_field(self) -> None:
        assert partial_string_field('{"coach": "tip", "reply": "real"}', "reply") == "real"

    def test_handles_an_empty_value(self) -> None:
        assert partial_string_field('{"reply": "", "done": true}', "reply") == ""


class _Delta:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str | None) -> None:
        self.delta = _Delta(content)


class _Chunk:
    def __init__(self, content: str | None) -> None:
        self.choices = [_Choice(content)]


class _FakeCompletions:
    def __init__(self, pieces: list[str], sink: list[dict]) -> None:
        self._pieces = pieces
        self._sink = sink

    async def create(self, **kwargs):
        self._sink.append(kwargs)

        async def gen():
            for piece in self._pieces:
                yield _Chunk(piece)

        class _Stream:
            def __aiter__(self_inner):
                return gen()

        return _Stream()


class _FakeClient:
    def __init__(self, pieces: list[str], sink: list[dict] | None = None) -> None:
        self.chat = type(
            "chat", (), {"completions": _FakeCompletions(pieces, sink if sink is not None else [])}
        )()


class TestStreamJsonContent:
    @pytest.mark.asyncio
    async def test_emits_only_the_watched_field_and_returns_raw_json(self) -> None:
        seen: list[str] = []
        pieces = ['{"reply": "', "Hello", " there", '", "coach": "', "unseen tip", '", "done": true}']

        raw = await stream_json_content(
            _FakeClient(pieces),
            field="reply",
            on_delta=lambda d: seen.append(d),
            model="m",
            messages=[],
        )

        assert seen == ["Hello", " there"]  # the coach tip is never published
        assert raw == "".join(pieces)

    @pytest.mark.asyncio
    async def test_requests_a_stream(self) -> None:
        calls: list[dict] = []
        await stream_json_content(
            _FakeClient(['{"say": "hi"}'], calls),
            field="say",
            on_delta=lambda d: None,
            model="m",
            messages=[],
            response_format={"type": "json_schema"},
        )
        assert calls[0]["stream"] is True
        assert calls[0]["response_format"] == {"type": "json_schema"}

    @pytest.mark.asyncio
    async def test_publishes_nothing_when_the_field_never_appears(self) -> None:
        seen: list[str] = []
        raw = await stream_json_content(
            _FakeClient(['{"other": "value"}']),
            field="reply",
            on_delta=lambda d: seen.append(d),
            model="m",
            messages=[],
        )
        assert seen == []
        assert raw == '{"other": "value"}'

    @pytest.mark.asyncio
    async def test_a_failing_sink_never_breaks_the_result(self) -> None:
        def explode(_: str) -> None:
            raise RuntimeError("subscriber gone")

        raw = await stream_json_content(
            _FakeClient(['{"reply": "still fine"}']),
            field="reply",
            on_delta=explode,
            model="m",
            messages=[],
        )
        assert raw == '{"reply": "still fine"}'

    @pytest.mark.asyncio
    async def test_survives_a_value_split_mid_escape(self) -> None:
        seen: list[str] = []
        raw = await stream_json_content(
            _FakeClient(['{"reply": "a\\', 'nb"}']),
            field="reply",
            on_delta=lambda d: seen.append(d),
            model="m",
            messages=[],
        )
        assert "".join(seen) == "a\nb"
        assert raw == '{"reply": "a\\nb"}'
