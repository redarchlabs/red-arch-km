"""Startup warnings emitted by Settings validators.

Covers the auth-bypass warning specifically: ``e2e_test_mode`` lets anyone holding the
shared secret authenticate as any user, so it being on outside debug must be visible in the
logs — and must NOT be a hard failure, because deployments already run with it enabled and
refusing to start would take them down instead of telling anyone why.
"""

import logging

import pytest

from api.config import Settings

pytestmark = pytest.mark.unit


def _settings(**overrides: object) -> Settings:
    return Settings(secret_key="x", **overrides)  # type: ignore[arg-type]


def test_e2e_test_mode_outside_debug_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="api.config"):
        settings = _settings(e2e_test_mode=True, debug=False)

    assert settings.e2e_test_mode is True, "the flag must still take effect"
    assert "E2E_TEST_MODE is enabled" in caplog.text
    assert "X-Test-User" in caplog.text, "the warning should name the actual mechanism"


def test_e2e_test_mode_outside_debug_does_not_raise() -> None:
    # The load-bearing assertion: a hard failure here would break every existing
    # deployment that runs with the bypass on, on its next restart.
    assert _settings(e2e_test_mode=True, debug=False).e2e_test_mode is True


def test_e2e_test_mode_in_debug_is_quiet(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="api.config"):
        _settings(e2e_test_mode=True, debug=True)

    assert "E2E_TEST_MODE" not in caplog.text, "local dev uses this constantly; don't nag"


def test_no_warning_when_bypass_is_off(caplog: pytest.LogCaptureFixture) -> None:
    # Passed explicitly, not left to the default: Settings also reads the developer's
    # .env, which sets API_E2E_TEST_MODE=true — so an implicit case here would assert
    # whatever the local machine happens to have configured.
    with caplog.at_level(logging.WARNING, logger="api.config"):
        settings = _settings(e2e_test_mode=False, debug=False)

    assert settings.e2e_test_mode is False
    assert "E2E_TEST_MODE" not in caplog.text


def test_bypass_is_declared_off_by_default() -> None:
    # The declared default, read off the field rather than an instance, so the ambient
    # environment cannot make this pass or fail.
    assert Settings.model_fields["e2e_test_mode"].default is False
