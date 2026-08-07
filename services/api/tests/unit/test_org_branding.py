"""Org branding: the settings contract and the share-link disclosure boundary.

Putting an org's name and logo on an anonymous link is a disclosure, not a
decoration — a share URL can be forwarded anywhere. These pin the two rules that
keep it a deliberate choice: branding is per-link and off by default, and the
logo route refuses a link that didn't opt in.
"""

from __future__ import annotations

import uuid

import pytest
from api.schemas.form import FormConfig
from api.schemas.org import OrgSettingsUpdate
from api.schemas.view import ViewShareRequest
from pydantic import ValidationError


class TestShareBrandingDefaults:
    def test_sharing_does_not_brand_unless_asked(self):
        """The disclosure is opt-in: enabling a link must not name the org."""
        assert ViewShareRequest().show_branding is False

    def test_branding_can_be_opted_into(self):
        assert ViewShareRequest(show_branding=True).show_branding is True


class TestAccentColorValidation:
    """The accent reaches a CSS custom property, so the value is validated
    rather than trusted — a non-hex string has no business being stored."""

    @pytest.mark.parametrize("value", ["#c2410c", "#FFFFFF", "#000000"])
    def test_accepts_a_hex_triple(self, value: str):
        assert OrgSettingsUpdate(accent_color=value).accent_color == value

    @pytest.mark.parametrize(
        "value",
        [
            "red",
            "#fff",  # shorthand: not the stored format
            "#12345g",
            "rgb(1,2,3)",
            "#c2410c; background: url(x)",  # the reason this is validated at all
        ],
    )
    def test_rejects_anything_else(self, value: str):
        with pytest.raises(ValidationError):
            OrgSettingsUpdate(accent_color=value)

    def test_null_clears_the_accent(self):
        body = OrgSettingsUpdate(accent_color=None)
        assert body.accent_color is None
        assert "accent_color" in body.model_fields_set  # explicit null, not omitted


class TestSettingsPatchSemantics:
    """Per-field, keyed on ``model_fields_set``: omitting a field must mean "no
    change". Without this the home-view form would wipe branding on every save
    (and vice versa), which is precisely the class of bug that made the view
    builder silently reset kiosk padding."""

    def test_omitted_fields_are_not_marked_as_sent(self):
        body = OrgSettingsUpdate(home_view_id=None)
        assert "home_view_id" in body.model_fields_set
        assert "accent_color" not in body.model_fields_set

    def test_each_field_can_be_sent_alone(self):
        accent_only = OrgSettingsUpdate(accent_color="#c2410c")
        assert "home_view_id" not in accent_only.model_fields_set


class TestViewThemeLock:
    def test_theme_defaults_to_following_the_viewer(self):
        """NULL keeps today's behaviour: the page follows the viewer's theme."""
        assert FormConfig(version=2, elements=[]).theme is None

    @pytest.mark.parametrize("theme", ["light", "dark", "redarch"])
    def test_theme_can_be_pinned(self, theme: str):
        assert FormConfig(version=2, elements=[], theme=theme).theme == theme

    def test_unknown_theme_is_rejected(self):
        with pytest.raises(ValidationError):
            FormConfig(version=2, elements=[], theme="midnight")

    def test_theme_survives_a_config_round_trip(self):
        """The builder rewrites the whole config on save; a pinned theme has to
        come back out the other side (see the padding regression)."""
        cfg = FormConfig.model_validate(
            {"version": 2, "elements": [], "theme": "dark", "padding": "comfortable"}
        )
        again = FormConfig.model_validate(cfg.model_dump(mode="json"))
        assert again.theme == "dark"
        assert again.padding == "comfortable"


def test_share_request_still_forbids_unknown_keys():
    """extra="forbid" is intact — show_branding didn't open the payload up."""
    with pytest.raises(ValidationError):
        ViewShareRequest(record_id=uuid.uuid4(), bogus=True)
