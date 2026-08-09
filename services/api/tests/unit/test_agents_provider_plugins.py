"""Out-of-tree LLM providers register themselves into the catalog.

This repository ships the three vendor APIs plus anything OpenAI-shaped, which is
what an open deployment should need — official APIs and a local server. A private
deployment may reach a model over a transport that has no business being here (an
internal gateway, a sidecar wrapping a subscription CLI). The seam below lets such
a transport register itself at startup so agents, the admin picker and the org
credential store treat it exactly like a built-in, while the code implementing it
stays in its own repository.

The registry is process-global, so every test restores it.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest
from api.services.agents.llm import catalog
from api.services.agents.llm.catalog import (
    ModelDef,
    ProviderDef,
    provider_for_model,
    providers,
    valid_providers,
)
from api.services.agents.llm.plugins import PluginLoadError, load_plugins
from api.services.agents.llm.routing import provider_for

pytestmark = pytest.mark.unit


SIDECAR = ProviderDef(
    "sidecar",
    "Private sidecar",
    (ModelDef("sidecar/house-agent", "House agent"),),
    "SIDECAR_TOKEN",
)


class _FakeTransport:
    """Stands in for a plugin's own transport — deliberately NOT an LLMProvider."""

    def __init__(self, settings: Any, model: str, api_key: str | None) -> None:
        self.settings, self.model, self.api_key = settings, model, api_key


@pytest.fixture(autouse=True)
def clean_registry():
    """Restore both halves of the global state a plugin touches.

    ``importlib.import_module`` is a no-op for an already-imported module, so the
    module cache has to be cleared alongside the registry — otherwise whichever
    test loads the fake plugin second finds an empty catalog and the pair passes
    or fails purely on execution order.
    """

    def _clear() -> None:
        catalog.reset_registry()
        sys.modules.pop("fake_llm_plugin", None)

    _clear()
    yield
    _clear()


@pytest.fixture
def registered():
    catalog.register_provider(SIDECAR, _FakeTransport)
    return SIDECAR


class TestTheBuiltInCatalogIsUnchangedWithoutPlugins:
    def test_only_the_shipped_providers_are_present(self) -> None:
        assert {p.name for p in providers()} == {"anthropic", "openai", "gemini"}
        assert valid_providers() == frozenset({"anthropic", "openai", "gemini"})

    def test_an_unknown_prefix_still_reads_as_openai(self) -> None:
        # LiteLLM's default. A model only leaves this rule by being registered.
        assert provider_for_model("sidecar/house-agent") == "openai"


class TestARegisteredProvider:
    def test_joins_the_catalog(self, registered) -> None:
        assert registered in providers()
        assert "sidecar" in valid_providers()

    def test_claims_its_own_models(self, registered) -> None:
        assert provider_for_model("sidecar/house-agent") == "sidecar"
        # …and takes nothing else with it.
        assert provider_for_model("gpt-5-mini") == "openai"
        assert provider_for_model("anthropic/claude-sonnet-5") == "anthropic"

    def test_builds_its_own_transport(self, registered) -> None:
        settings = SimpleNamespace(openai_base_url="", openai_model_routes="")

        transport = provider_for(settings, "sidecar/house-agent", "tok")

        assert isinstance(transport, _FakeTransport)
        assert (transport.model, transport.api_key) == ("sidecar/house-agent", "tok")

    def test_does_not_capture_the_built_in_paths(self, registered) -> None:
        from api.services.agents.llm.provider import LLMProvider

        settings = SimpleNamespace(openai_base_url="", openai_model_routes="")
        assert isinstance(provider_for(settings, "gpt-5-mini", "k"), LLMProvider)

    def test_cannot_shadow_a_built_in(self) -> None:
        # Silently replacing "anthropic" would reroute every Claude agent in the
        # deployment, so it is refused rather than allowed to win.
        clash = ProviderDef("anthropic", "Impostor", (ModelDef("anthropic/x", "X"),), "NOPE")
        with pytest.raises(ValueError, match="anthropic"):
            catalog.register_provider(clash, _FakeTransport)


class TestLoadingPlugins:
    def test_nothing_configured_registers_nothing(self) -> None:
        assert load_plugins("") == []
        assert valid_providers() == frozenset({"anthropic", "openai", "gemini"})

    def test_a_module_is_imported_so_it_can_register(self) -> None:
        loaded = load_plugins("fake_llm_plugin")

        assert loaded == ["fake_llm_plugin"]
        assert "fake-sidecar" in valid_providers()

    def test_several_modules_are_comma_separated(self) -> None:
        assert load_plugins(" fake_llm_plugin , ") == ["fake_llm_plugin"]

    def test_a_missing_module_fails_loudly_at_startup(self) -> None:
        # Degrading quietly would leave every agent on that provider erroring one
        # run at a time, long after the deploy that caused it.
        with pytest.raises(PluginLoadError, match="no.such.module"):
            load_plugins("no.such.module")
