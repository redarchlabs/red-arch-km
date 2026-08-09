"""Load out-of-tree LLM providers named by ``LLM_PROVIDER_PLUGINS``.

This repository ships the official vendor APIs and, through the OpenAI shape, any
self-hosted server — the surface an open deployment should need. A deployment that
reaches a model over some other transport (an internal gateway, a sidecar wrapping
a subscription CLI) keeps that code in its own repository and names the module here
instead::

    LLM_PROVIDER_PLUGINS=acme_km2_sidecar.provider

Loading is nothing more than importing the module. The module registers itself at
import time by calling :func:`~api.services.agents.llm.catalog.register_provider`,
which is the entire contract — no base class to inherit, no interface to import
beyond the two dataclasses describing what it offers. Its transport object needs
only the ``stream``/``complete`` methods the runtime actually calls; see
:class:`~api.services.agents.llm.provider.LLMProvider` for the reference shape.

A configured module that cannot be imported raises at startup rather than being
skipped. Degrading quietly would leave the deployment running with agents pinned
to a provider that no longer exists, failing one run at a time long after the
deploy that caused it.
"""

from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)


class PluginLoadError(RuntimeError):
    """A module named in ``LLM_PROVIDER_PLUGINS`` could not be imported."""


def load_plugins(configured: str) -> list[str]:
    """Import each module named in ``configured``; return the names loaded.

    Accepts the raw setting: a comma-separated list, blanks ignored. Importing an
    already-imported module is a no-op, so calling this twice cannot double-register.
    """
    names = [part.strip() for part in (configured or "").split(",") if part.strip()]
    for name in names:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - any import failure is fatal config
            raise PluginLoadError(f"could not load LLM provider plugin '{name}': {exc}") from exc
        logger.info("loaded LLM provider plugin %s", name)
    return names
