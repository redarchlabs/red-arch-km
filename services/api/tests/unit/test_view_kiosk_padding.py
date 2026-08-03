"""Unit tests: a chrome-free view can ask for breathing room.

The kiosk and public-share routes render the element tree full-bleed — correct for a
control surface built to fill a screen (a crew station, a puzzle pad), wrong for a page
of prose, which ends up typeset hard against the bezel of a wall display.

``padding`` is therefore opt-in PER VIEW. These pin that default, because a global
default would silently reflow every kiosk built before the option existed.
"""

from __future__ import annotations

import pytest
from api.schemas.form import FormConfig
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def test_padding_defaults_to_full_bleed() -> None:
    """The existing edge-to-edge kiosks must keep their layout untouched."""
    assert FormConfig(version=2, elements=[]).padding == "none"


def test_a_config_written_before_the_option_existed_still_parses() -> None:
    # extra="forbid" cuts both ways: the field must be optional on the way IN, or every
    # stored view breaks the moment the schema gains a key.
    assert FormConfig.model_validate({"version": 2, "elements": [], "refresh_ms": 2000}).padding == "none"


@pytest.mark.parametrize("value", ["none", "comfortable", "spacious"])
def test_supported_paddings_round_trip(value: str) -> None:
    assert FormConfig(version=2, elements=[], padding=value).padding == value


def test_an_unknown_padding_is_rejected_rather_than_ignored() -> None:
    """A typo must fail loudly here — the renderer maps this straight to a class name,
    so an unrecognised value would silently render as no padding at all."""
    with pytest.raises(ValidationError):
        FormConfig(version=2, elements=[], padding="roomy")


def test_padding_is_independent_of_refresh() -> None:
    config = FormConfig(version=2, elements=[], padding="comfortable", refresh_ms=2000)
    assert (config.padding, config.refresh_ms) == ("comfortable", 2000)
