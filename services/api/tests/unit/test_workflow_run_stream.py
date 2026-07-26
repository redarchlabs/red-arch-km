"""Live token stream for a workflow run: channel scoping, publisher, wiring.

The security property under test is that the channel carrying an answer's tokens
is namespaced by ORG, and that a subscriber derives the channel from its own
request context — so a stream token belonging to another org is unreadable even
when it is known.
"""

from __future__ import annotations

import json
import uuid

import pytest

from api.services.workflow.stream import (
    EVENT_DELTA,
    EVENT_DONE,
    RunStreamPublisher,
    channel_for,
    is_valid_token,
)


class _FakeRedis:
    def __init__(self, fail: bool = False) -> None:
        self.published: list[tuple[str, str]] = []
        self._fail = fail

    async def publish(self, channel: str, payload: str) -> None:
        if self._fail:
            raise RuntimeError("redis is down")
        self.published.append((channel, payload))


class TestChannelScoping:
    def test_channel_includes_the_org(self) -> None:
        org = uuid.uuid4()
        token = str(uuid.uuid4())
        assert str(org) in channel_for(org, token)
        assert token in channel_for(org, token)

    def test_same_token_in_two_orgs_is_two_channels(self) -> None:
        """The whole authorization model: a stolen token reads a different channel."""
        token = str(uuid.uuid4())
        assert channel_for(uuid.uuid4(), token) != channel_for(uuid.uuid4(), token)

    @pytest.mark.parametrize(
        "token",
        [
            "not-a-uuid",
            "",
            "*",  # would turn a psubscribe into a wildcard
            "../etc",
            "wf:stream:other",  # attempt to inject a channel separator
        ],
    )
    def test_rejects_tokens_that_are_not_uuids(self, token: str) -> None:
        assert is_valid_token(token) is False

    def test_accepts_a_uuid(self) -> None:
        assert is_valid_token(str(uuid.uuid4())) is True


class TestPublisher:
    @pytest.mark.asyncio
    async def test_publishes_deltas_and_done_to_the_org_channel(self) -> None:
        redis = _FakeRedis()
        org = uuid.uuid4()
        token = str(uuid.uuid4())
        publisher = RunStreamPublisher(redis, org, token)

        await publisher.delta("Operation ")
        await publisher.delta("Deep Horizon.")
        await publisher.done()

        assert [c for c, _ in redis.published] == [channel_for(org, token)] * 3
        payloads = [json.loads(p) for _, p in redis.published]
        assert payloads[0] == {"type": EVENT_DELTA, "text": "Operation "}
        assert payloads[1] == {"type": EVENT_DELTA, "text": "Deep Horizon."}
        assert payloads[2] == {"type": EVENT_DONE}

    @pytest.mark.asyncio
    async def test_skips_empty_deltas(self) -> None:
        redis = _FakeRedis()
        await RunStreamPublisher(redis, uuid.uuid4(), str(uuid.uuid4())).delta("")
        assert redis.published == []

    @pytest.mark.asyncio
    async def test_a_dead_redis_never_raises(self) -> None:
        """Publishing is best-effort — the answer must survive a broken preview."""
        publisher = RunStreamPublisher(_FakeRedis(fail=True), uuid.uuid4(), str(uuid.uuid4()))
        await publisher.delta("text")  # must not raise
        await publisher.done()


class _FakePubSub:
    """Serves queued messages, then `None`s (an idle channel)."""

    def __init__(self, messages: list[dict | None]) -> None:
        self._messages = list(messages)
        self.subscribed: list[str] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def get_message(self, **_: object) -> dict | None:
        return self._messages.pop(0) if self._messages else None

    async def aclose(self) -> None:
        self.closed = True


class _PubSubRedis:
    def __init__(self, messages: list[dict | None]) -> None:
        self.pubsub_obj = _FakePubSub(messages)

    def pubsub(self) -> _FakePubSub:
        return self.pubsub_obj


def _msg(payload: str) -> dict:
    return {"data": payload}


class TestSseFrames:
    @pytest.mark.asyncio
    async def test_forwards_deltas_then_stops_on_done(self) -> None:
        from api.services.workflow.stream import sse_frames

        redis = _PubSubRedis(
            [
                _msg(json.dumps({"type": EVENT_DELTA, "text": "Deep "})),
                _msg(json.dumps({"type": EVENT_DELTA, "text": "Horizon"})),
                _msg(json.dumps({"type": EVENT_DONE})),
                _msg(json.dumps({"type": EVENT_DELTA, "text": "never sent"})),
            ]
        )

        frames = [frame async for frame in sse_frames(redis, "wf:stream:org:token")]

        assert b"event: delta\ndata: " in frames[0]
        assert b"Deep " in frames[0]
        assert b"Horizon" in frames[1]
        assert frames[-1].startswith(b"event: done")
        assert len(frames) == 3  # nothing after done
        assert redis.pubsub_obj.subscribed == ["wf:stream:org:token"]
        assert redis.pubsub_obj.closed is True

    @pytest.mark.asyncio
    async def test_skips_malformed_payloads_without_breaking_framing(self) -> None:
        from api.services.workflow.stream import sse_frames

        redis = _PubSubRedis(
            [
                _msg("not json at all"),
                _msg(json.dumps({"type": EVENT_DELTA, "text": "ok"})),
                _msg(json.dumps({"type": EVENT_DONE})),
            ]
        )

        frames = [frame async for frame in sse_frames(redis, "c")]

        assert all(b"not json" not in f for f in frames)
        assert b"ok" in frames[0]

    @pytest.mark.asyncio
    async def test_emits_keepalives_while_the_run_is_still_thinking(self, monkeypatch) -> None:
        """An idle channel must not look like a dead connection."""
        import api.services.workflow.stream as stream_mod
        from api.services.workflow.stream import sse_frames

        # Shrink the cap so the idle path terminates promptly under test.
        monkeypatch.setattr(stream_mod, "STREAM_TIMEOUT_SECONDS", 0.05)
        redis = _PubSubRedis([None, None])

        frames = [frame async for frame in sse_frames(redis, "c")]

        assert frames[0] == b": keepalive\n\n"
        assert frames[-1].startswith(b"event: done")  # timeout closes the stream
        assert redis.pubsub_obj.closed is True

    @pytest.mark.asyncio
    async def test_a_broken_subscription_yields_an_error_frame(self) -> None:
        from api.services.workflow.stream import sse_frames

        class _Broken:
            def pubsub(self):
                class _P:
                    async def subscribe(self, _channel):
                        raise RuntimeError("redis is down")

                    async def aclose(self):
                        return None

                return _P()

        frames = [frame async for frame in sse_frames(_Broken(), "c")]
        assert frames == [b'event: error\ndata: {"detail": "stream failed"}\n\n']


class TestRunnerWiring:
    """The summarize action streams only when the run has a watcher."""

    @pytest.mark.asyncio
    async def test_summarize_passes_no_sink_without_a_watcher(self, monkeypatch) -> None:
        captured = await self._summarize_with(monkeypatch, delta_sink=None)
        assert captured["on_delta"] is None

    @pytest.mark.asyncio
    async def test_summarize_passes_the_publisher_delta_when_watched(self, monkeypatch) -> None:
        publisher = RunStreamPublisher(_FakeRedis(), uuid.uuid4(), str(uuid.uuid4()))
        captured = await self._summarize_with(monkeypatch, delta_sink=publisher)
        assert captured["on_delta"] == publisher.delta

    async def _summarize_with(self, monkeypatch, *, delta_sink) -> dict:
        from api.services import spoken_summary
        from api.services.workflow.runner import ActionExecutor

        captured: dict = {}

        async def fake_summarize(client, model, **kwargs):
            captured.update(kwargs)
            return "summary"

        monkeypatch.setattr(spoken_summary, "summarize_for_speech", fake_summarize)
        monkeypatch.setattr(
            "api.services.workflow.runner.make_async_openai", lambda settings, key: object()
        )

        class _Settings:
            openai_summary_model = "gpt-5-nano"

            class openai_api_key:  # noqa: N801 - mimics SecretStr on Settings
                @staticmethod
                def get_secret_value() -> str:
                    return "sk-test"

        executor = ActionExecutor(None, settings=_Settings(), delta_sink=delta_sink)  # type: ignore[arg-type]
        monkeypatch.setattr(executor, "_org_openai_key", lambda org_id: _none())
        await executor._summarize(uuid.uuid4(), {"text": "some text"})
        return captured


async def _none() -> None:
    return None
