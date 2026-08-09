"""What the live transcript publishes, and what it deliberately does not.

``_persist_event`` keeps the durable record and drops every ``delta``, so a
background run's reasoning never left the process. This publishes it beside that,
without becoming load-bearing: nobody watching must never be a reason for a run to
fail.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from api.services.agents.live import activity

pytestmark = pytest.mark.unit


class _Redis:
    def __init__(self, *, fail: bool = False) -> None:
        self.published: list[tuple[str, dict]] = []
        self._fail = fail

    async def publish(self, channel: str, payload: str) -> None:
        if self._fail:
            raise RuntimeError("redis is down")
        self.published.append((channel, json.loads(payload)))


ORG = uuid.uuid4()
RUN = uuid.uuid4()
WO = uuid.uuid4()


def _publisher(redis, *, work_order_id=WO):
    return activity.RunActivityPublisher(redis, ORG, RUN, agent_name="chief", work_order_id=work_order_id)


class TestChannels:
    def test_the_org_is_inside_the_channel_name(self) -> None:
        """The authz trick: a run id from another org resolves to a channel nobody
        publishes on, so a subscriber cannot reach across orgs by guessing."""
        assert str(ORG) in activity.run_channel(ORG, RUN)
        assert activity.run_channel(uuid.uuid4(), RUN) != activity.run_channel(ORG, RUN)

    def test_it_does_not_collide_with_the_console_wake_channel(self) -> None:
        """bus.run_channel carries id-only wakes whose recipient re-reads Postgres.
        Sharing a name would make the console do that on every token."""
        from api.services.agents.live import bus

        assert activity.run_channel(ORG, RUN) != bus.run_channel(ORG, RUN)


class TestPublishing:
    async def test_deltas_are_coalesced(self) -> None:
        # One Redis message per token would turn a fast model into thousands of
        # publishes for text a person reads in chunks anyway.
        redis = _Redis()
        pub = _publisher(redis)

        for token in ("Hel", "lo ", "world"):
            await pub.publish({"type": "delta", "content": token})
        await pub.close()

        # Counted on one channel: a publish fans out to the run's and the work
        # order's, so two messages is one flush.
        own = [p for c, p in redis.published if c == activity.run_channel(ORG, RUN)]
        assert [p["content"] for p in own if p["type"] == "delta"] == ["Hello world"]

    async def test_a_tool_call_flushes_the_text_before_it(self) -> None:
        """So the transcript keeps its order: the reasoning that led to a call is
        shown before the call, not after it."""
        redis = _Redis()
        pub = _publisher(redis)

        await pub.publish({"type": "delta", "content": "I should search."})
        await pub.publish({"type": "tool_call", "name": "search_knowledge", "arguments": {}})
        await pub.close()

        own = [p["type"] for c, p in redis.published if c == activity.run_channel(ORG, RUN)]
        assert own == ["delta", "tool_call"]

    async def test_it_reaches_both_the_run_and_the_work_order(self) -> None:
        # The work-order channel is what lets one page watch every agent on an
        # order without opening a socket per run.
        redis = _Redis()
        pub = _publisher(redis)

        await pub.publish({"type": "done"})

        assert {c for c, _ in redis.published} == {
            activity.run_channel(ORG, RUN),
            activity.work_order_channel(ORG, WO),
        }

    async def test_a_run_with_no_work_order_publishes_only_to_its_own(self) -> None:
        redis = _Redis()
        pub = _publisher(redis, work_order_id=None)

        await pub.publish({"type": "done"})

        assert [c for c, _ in redis.published] == [activity.run_channel(ORG, RUN)]

    async def test_internal_events_are_not_leaked_to_browsers(self) -> None:
        """The emit vocabulary is internal and free to grow; a whitelist keeps a
        new one from reaching a browser by default."""
        redis = _Redis()
        pub = _publisher(redis)

        await pub.publish({"type": "some_future_internal_event", "secret": "x"})

        assert redis.published == []

    async def test_redis_being_down_never_reaches_the_run(self) -> None:
        # This is bolted onto the executor's emit. A live view nobody is watching
        # must not be able to fail the work.
        pub = _publisher(_Redis(fail=True))

        await pub.publish({"type": "delta", "content": "hi"})
        await pub.close()  # must not raise

    async def test_the_last_tokens_survive_the_end_of_a_run(self) -> None:
        """Whatever ends a run, the final buffered tokens are the ones explaining
        why — the moment they matter most is the moment a timer would drop them."""
        redis = _Redis()
        pub = _publisher(redis)
        await pub.publish({"type": "delta", "content": "almost done"})

        await pub.close()

        assert any(p["type"] == "delta" and p["content"] == "almost done" for _, p in redis.published)

    async def test_a_slow_reader_still_gets_the_text_on_the_timer(self) -> None:
        redis = _Redis()
        pub = _publisher(redis)

        await pub.publish({"type": "delta", "content": "tick"})
        await asyncio.sleep(activity.DELTA_FLUSH_SECONDS * 3)

        assert any(p["type"] == "delta" for _, p in redis.published)
