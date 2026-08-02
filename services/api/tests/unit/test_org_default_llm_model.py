"""Org-wide default LLM model (orgs.default_llm_model).

Proves the resolution order every LLM call site follows — explicit per-call
model > org pin > env default — plus the repo's set/clear semantics and the
site-admin model catalog endpoint that feeds the org settings UI.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from api.config import Settings
from api.repositories.org import OrgRepository
from api.routers.orgs import list_llm_models
from api.services.org_llm import org_default_llm_model
from api.services.workflow.runner import ActionExecutor

ORG_ID = uuid.uuid4()


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "secret_key": "test-secret",
        "database_url": "postgresql+asyncpg://t:t@localhost/t",
        "openai_api_key": "sk-test",
    }
    values.update(overrides)
    return Settings(**values)


def _session_returning(org: Any) -> MagicMock:
    session = MagicMock()
    session.get = AsyncMock(return_value=org)
    return session


class TestOrgDefaultLlmModelHelper:
    @pytest.mark.asyncio
    async def test_returns_the_org_pin(self) -> None:
        session = _session_returning(SimpleNamespace(default_llm_model="qwen3-30b"))
        assert await org_default_llm_model(session, ORG_ID) == "qwen3-30b"

    @pytest.mark.asyncio
    async def test_unpinned_org_is_none(self) -> None:
        session = _session_returning(SimpleNamespace(default_llm_model=None))
        assert await org_default_llm_model(session, ORG_ID) is None

    @pytest.mark.asyncio
    async def test_missing_org_is_none(self) -> None:
        assert await org_default_llm_model(_session_returning(None), ORG_ID) is None

    @pytest.mark.asyncio
    async def test_accepts_string_org_id(self) -> None:
        session = _session_returning(SimpleNamespace(default_llm_model="gpt-4.1-mini"))
        assert await org_default_llm_model(session, str(ORG_ID)) == "gpt-4.1-mini"


class TestActionExecutorResolution:
    """The workflow engine's precedence: node config.model > org pin > env default."""

    def _executor(self, org: Any, settings: Settings) -> ActionExecutor:
        return ActionExecutor(_session_returning(org), settings=settings)

    @pytest.mark.asyncio
    async def test_org_pin_beats_env_default(self) -> None:
        executor = self._executor(SimpleNamespace(default_llm_model="qwen3-30b", openai_api_key=None), _settings())
        seen: dict[str, Any] = {}

        async def _decide(client: Any, model: str, **kwargs: Any) -> dict[str, Any]:
            seen["model"] = model
            return {"say": "ok"}

        with (
            patch("api.services.llm_decide.decide_action", _decide),
            patch("api.services.workflow.runner.make_async_openai", MagicMock()),
        ):
            await executor._decide(ORG_ID, {"question": "q", "context": "c"})
        assert seen["model"] == "qwen3-30b"

    @pytest.mark.asyncio
    async def test_explicit_node_model_beats_the_org_pin(self) -> None:
        executor = self._executor(SimpleNamespace(default_llm_model="qwen3-30b", openai_api_key=None), _settings())
        seen: dict[str, Any] = {}

        async def _decide(client: Any, model: str, **kwargs: Any) -> dict[str, Any]:
            seen["model"] = model
            return {"say": "ok"}

        with (
            patch("api.services.llm_decide.decide_action", _decide),
            patch("api.services.workflow.runner.make_async_openai", MagicMock()),
        ):
            await executor._decide(ORG_ID, {"question": "q", "context": "c", "model": "gpt-5-nano"})
        assert seen["model"] == "gpt-5-nano"

    @pytest.mark.asyncio
    async def test_unpinned_org_falls_back_to_env_default(self) -> None:
        executor = self._executor(SimpleNamespace(default_llm_model=None, openai_api_key=None), _settings())
        seen: dict[str, Any] = {}

        async def _decide(client: Any, model: str, **kwargs: Any) -> dict[str, Any]:
            seen["model"] = model
            return {"say": "ok"}

        with (
            patch("api.services.llm_decide.decide_action", _decide),
            patch("api.services.workflow.runner.make_async_openai", MagicMock()),
        ):
            await executor._decide(ORG_ID, {"question": "q", "context": "c"})
        assert seen["model"] == _settings().openai_model


class TestRepositoryUpdateSemantics:
    def _repo_and_org(self) -> tuple[OrgRepository, SimpleNamespace]:
        session = MagicMock()
        session.flush = AsyncMock()
        org = SimpleNamespace(default_llm_model="old-model")
        return OrgRepository(session), org

    @pytest.mark.asyncio
    async def test_value_sets_the_pin(self) -> None:
        repo, org = self._repo_and_org()
        await repo.update(org, default_llm_model="qwen3-30b")  # type: ignore[arg-type]
        assert org.default_llm_model == "qwen3-30b"

    @pytest.mark.asyncio
    async def test_empty_string_clears_to_platform_default(self) -> None:
        repo, org = self._repo_and_org()
        await repo.update(org, default_llm_model="")  # type: ignore[arg-type]
        assert org.default_llm_model is None

    @pytest.mark.asyncio
    async def test_none_means_no_change(self) -> None:
        repo, org = self._repo_and_org()
        await repo.update(org, default_llm_model=None)  # type: ignore[arg-type]
        assert org.default_llm_model == "old-model"

    @pytest.mark.asyncio
    async def test_whitespace_only_clears(self) -> None:
        repo, org = self._repo_and_org()
        await repo.update(org, default_llm_model="   ")  # type: ignore[arg-type]
        assert org.default_llm_model is None


class TestLlmModelCatalogEndpoint:
    @pytest.mark.asyncio
    async def test_lists_routed_models_plus_defaults_deduped(self) -> None:
        settings = _settings(
            openai_model_routes="qwen3-30b=http://127.0.0.1:8099/v1, gpt-4.1-mini=https://api.openai.com/v1",
        )
        result = await list_llm_models(MagicMock(), settings)
        assert result["default"] == settings.openai_model
        assert "qwen3-30b" in result["models"]
        assert "gpt-4.1-mini" in result["models"]
        assert settings.openai_model in result["models"]
        assert len(result["models"]) == len(set(result["models"]))

    @pytest.mark.asyncio
    async def test_no_routes_still_offers_the_defaults(self) -> None:
        settings = _settings()
        result = await list_llm_models(MagicMock(), settings)
        assert settings.openai_model in result["models"]
        assert settings.openai_summary_model in result["models"]
