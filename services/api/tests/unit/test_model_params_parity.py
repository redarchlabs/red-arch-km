"""`api` and `shared_config` must agree about which models are reasoning models.

There are two implementations of this rule on purpose. `api.services.agents.llm.
reasoning` owns the agent-facing policy (effort tiers, per-agent overrides) and
keys off the provider catalog, which knows about runtime-registered providers.
`shared_config.model_params` owns the bare provider facts and is what a service
outside `api` — `brain_api` — can import.

Two copies of a classification is exactly how this broke: brain_api sent
`max_tokens` and `temperature=0.3` to a gpt-5.6 model and failed every RAG answer
with "Streaming failed", because nothing tied its behaviour to the rules `api`
had already worked out. Splitting them again is fine; letting them disagree is
not, so the disagreement is a test failure rather than a 400 in front of a user.
"""

from __future__ import annotations

import pytest
from api.services.agents.llm.reasoning import is_reasoning_model as api_is_reasoning
from api.services.agents.llm.reasoning import temperature_for as api_temperature_for
from shared_config.model_params import is_reasoning_model as shared_is_reasoning
from shared_config.model_params import temperature_for as shared_temperature_for

# Every id either half is likely to meet: the OpenAI families, the local llama.cpp
# models this deployment serves, prefixed ids from the agent roster, and the
# non-reasoning sibling that must not match on its prefix.
MODELS = (
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5-chat-latest",
    "o1",
    "o3",
    "o4-mini",
    "gpt-4.1-mini",
    "gpt-4o",
    "gpt-4o-mini",
    "qwen3-30b",
    "qwen3-4b-fast",
    "openai/gpt-5-mini",
    "openai/gpt-4.1-mini",
    "openai/qwen3-30b",
    "anthropic/claude-opus-4",
    "gemini/gemini-2.5-pro",
)


@pytest.mark.parametrize("model", MODELS)
def test_both_halves_classify_the_same_way(model: str) -> None:
    assert shared_is_reasoning(model) == api_is_reasoning(model), (
        f"{model} is a reasoning model to one half and not the other — "
        "one of them will send a parameter the provider refuses"
    )


@pytest.mark.parametrize("model", MODELS)
def test_both_halves_agree_on_temperature(model: str) -> None:
    assert shared_temperature_for(model, 0.3) == api_temperature_for(model, 0.3)
