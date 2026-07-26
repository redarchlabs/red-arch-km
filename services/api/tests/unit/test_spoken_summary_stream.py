"""Token-streaming path of ``summarize_for_speech``.

The non-streaming behaviour is covered in test_workflow_knowledge_search.py; these
tests pin the contract that matters to the streaming robot chat: deltas are handed
to ``on_delta`` as they arrive, and the assembled reply is still the return value,
so a caller that ignores streaming sees no behavioural change.
"""

from __future__ import annotations

import pytest

from api.services.spoken_summary import summarize_for_speech


class _Delta:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str | None) -> None:
        self.delta = _Delta(content)


class _Chunk:
    def __init__(self, content: str | None) -> None:
        self.choices = [_Choice(content)]


class _FakeStream:
    """Async iterator of chunks, mimicking the OpenAI streaming response."""

    def __init__(self, pieces: list[str | None]) -> None:
        self._pieces = pieces

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for piece in self._pieces:
            yield _Chunk(piece)


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _NonStreamChoice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)


class _NonStreamResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_NonStreamChoice(content)]


class _FakeCompletions:
    def __init__(self, pieces: list[str | None], sink: list[dict]) -> None:
        self._pieces = pieces
        self._sink = sink

    async def create(self, **kwargs):
        self._sink.append(kwargs)
        if kwargs.get("stream"):
            return _FakeStream(self._pieces)
        return _NonStreamResponse("".join(p for p in self._pieces if p))


class _FakeClient:
    def __init__(self, pieces: list[str | None], sink: list[dict] | None = None) -> None:
        self.chat = type(
            "chat", (), {"completions": _FakeCompletions(pieces, sink if sink is not None else [])}
        )()


class TestStreamingSummary:
    @pytest.mark.asyncio
    async def test_emits_each_delta_and_returns_the_assembled_reply(self) -> None:
        seen: list[str] = []
        client = _FakeClient(["Operation ", "Deep ", "Horizon."])

        out = await summarize_for_speech(
            client,
            "gpt-5-nano",
            text="Operation Deep Horizon is a rescue mission.",
            on_delta=lambda d: seen.append(d),
        )

        assert seen == ["Operation ", "Deep ", "Horizon."]
        assert out == "Operation Deep Horizon."

    @pytest.mark.asyncio
    async def test_requests_a_stream_only_when_a_sink_is_given(self) -> None:
        calls: list[dict] = []
        await summarize_for_speech(_FakeClient(["hi"], calls), "gpt-5-nano", text="t")
        assert not calls[0].get("stream")

        calls.clear()
        await summarize_for_speech(
            _FakeClient(["hi"], calls), "gpt-5-nano", text="t", on_delta=lambda d: None
        )
        assert calls[0].get("stream") is True

    @pytest.mark.asyncio
    async def test_tolerates_empty_and_missing_deltas(self) -> None:
        seen: list[str] = []
        client = _FakeClient(["Hello", None, "", " there"])

        out = await summarize_for_speech(
            client, "gpt-5-nano", text="t", on_delta=lambda d: seen.append(d)
        )

        assert seen == ["Hello", " there"]  # empty/None chunks are not published
        assert out == "Hello there"

    @pytest.mark.asyncio
    async def test_falls_back_to_input_when_the_stream_yields_nothing(self) -> None:
        out = await summarize_for_speech(
            _FakeClient([]), "gpt-5-nano", text="fallback text", on_delta=lambda d: None
        )
        assert out == "fallback text"

    @pytest.mark.asyncio
    async def test_a_failing_sink_never_breaks_the_answer(self) -> None:
        """Publishing is best-effort: a dead subscriber must not fail the run."""

        def explode(_: str) -> None:
            raise RuntimeError("redis is down")

        out = await summarize_for_speech(
            _FakeClient(["still ", "works"]), "gpt-5-nano", text="t", on_delta=explode
        )
        assert out == "still works"

    @pytest.mark.asyncio
    async def test_supports_an_async_sink(self) -> None:
        seen: list[str] = []

        async def sink(delta: str) -> None:
            seen.append(delta)

        out = await summarize_for_speech(
            _FakeClient(["a", "b"]), "gpt-5-nano", text="t", on_delta=sink
        )
        assert seen == ["a", "b"]
        assert out == "ab"

    @pytest.mark.asyncio
    async def test_still_pins_reasoning_effort_when_streaming(self) -> None:
        calls: list[dict] = []
        await summarize_for_speech(
            _FakeClient(["x"], calls), "gpt-5-nano", text="t", on_delta=lambda d: None
        )
        assert calls[0].get("reasoning_effort") == "minimal"
