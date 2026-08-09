"""A stand-in for an out-of-tree provider package, imported by the plugin tests.

Real plugins live in their own repository; this one exists only to prove that
importing a module is all it takes for its provider to join the catalog.
"""

from __future__ import annotations

from typing import Any

from api.services.agents.llm.catalog import ModelDef, ProviderDef, register_provider


class FakeTransport:
    def __init__(self, settings: Any, model: str, api_key: str | None) -> None:
        self.settings, self.model, self.api_key = settings, model, api_key


register_provider(
    ProviderDef(
        "fake-sidecar",
        "Fake sidecar",
        (ModelDef("fake-sidecar/agent", "Fake agent"),),
        "FAKE_SIDECAR_TOKEN",
    ),
    FakeTransport,
)
