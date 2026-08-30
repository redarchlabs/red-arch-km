"""Unit tests for the per-view appearance override.

The appearance block is the ONE styling hook a view definition gets. It exists so
an org can dress a view — a bridge station, a wall board, a branded share page —
without the platform learning anything about that org's look. Everything here is
therefore a closed vocabulary: a named token gets a validated value, and nothing
else reaches the DOM.

The validation is the security boundary, not a nicety. These values are rendered
into a `style` attribute, so a free-form string would be CSS injection: a value
carrying `}` could close the rule and open its own, and a `url(...)` could beacon
the viewer's IP to a third party on page load. Hence: keys from an allow-list,
colors matched against a hex pattern, everything else an enum or a bounded int.
"""

from __future__ import annotations

import pytest
from api.schemas.form import AppearanceConfig, FormConfig
from pydantic import ValidationError


class TestColors:
    def test_accepts_allowlisted_token_with_hex_value(self):
        a = AppearanceConfig(colors={"primary": "#233f7a"})
        assert a.colors == {"primary": "#233f7a"}

    def test_accepts_three_digit_hex(self):
        assert AppearanceConfig(colors={"border": "#abc"}).colors == {"border": "#abc"}

    def test_rejects_unknown_token(self):
        # An unknown key would emit an arbitrary `--color-<key>` custom property.
        with pytest.raises(ValidationError, match="unknown color token"):
            AppearanceConfig(colors={"not-a-token": "#000000"})

    @pytest.mark.parametrize(
        "value",
        [
            "red",  # keyword: not a hex triple
            "#12345",  # wrong length
            "#gggggg",  # not hex digits
            "rgb(0,0,0)",  # function syntax
            "#000; background: url(https://evil.example/beacon)",  # injection
            "#000000}html{display:none",  # rule breakout
            "url(javascript:alert(1))",
            "",
        ],
    )
    def test_rejects_non_hex_value(self, value):
        with pytest.raises(ValidationError, match="must be a hex color"):
            AppearanceConfig(colors={"primary": value})


class TestTreatments:
    def test_defaults_are_all_none(self):
        # An absent appearance block must change nothing about how a view renders.
        a = AppearanceConfig()
        assert a.colors == {}
        assert a.surface is None
        assert a.button_finish is None
        assert a.texture is None
        assert a.heading_case is None
        assert a.radius_px is None

    @pytest.mark.parametrize("surface", ["flat", "glass"])
    def test_surface_enum(self, surface):
        assert AppearanceConfig(surface=surface).surface == surface

    def test_rejects_unknown_surface(self):
        with pytest.raises(ValidationError):
            AppearanceConfig(surface="frosted-lucite")

    @pytest.mark.parametrize("finish", ["flat", "gradient"])
    def test_button_finish_enum(self, finish):
        assert AppearanceConfig(button_finish=finish).button_finish == finish

    @pytest.mark.parametrize("texture", ["none", "diamond", "grid"])
    def test_texture_enum(self, texture):
        assert AppearanceConfig(texture=texture).texture == texture

    @pytest.mark.parametrize("case", ["none", "uppercase", "capitalize"])
    def test_heading_case_enum(self, case):
        assert AppearanceConfig(heading_case=case).heading_case == case

    @pytest.mark.parametrize("px", [0, 15, 48])
    def test_radius_accepts_bounded_px(self, px):
        assert AppearanceConfig(radius_px=px).radius_px == px

    @pytest.mark.parametrize("px", [-1, 49, 9999])
    def test_radius_rejects_out_of_range(self, px):
        with pytest.raises(ValidationError):
            AppearanceConfig(radius_px=px)

    def test_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            AppearanceConfig(font_family="Comic Sans")


class TestOnFormConfig:
    def test_absent_by_default(self):
        assert FormConfig().appearance is None

    def test_round_trips_through_form_config(self):
        cfg = FormConfig.model_validate(
            {
                "elements": [],
                "appearance": {
                    "colors": {"primary": "#233f7a", "border": "#9c9da0"},
                    "surface": "glass",
                    "button_finish": "gradient",
                    "texture": "diamond",
                    "heading_case": "capitalize",
                    "radius_px": 15,
                },
            }
        )
        assert cfg.appearance is not None
        assert cfg.appearance.colors["primary"] == "#233f7a"
        assert cfg.appearance.surface == "glass"
        assert cfg.appearance.radius_px == 15
        # and it survives a serialize/parse cycle unchanged
        assert FormConfig.model_validate(cfg.model_dump()).appearance == cfg.appearance

    def test_invalid_appearance_rejects_the_whole_config(self):
        with pytest.raises(ValidationError):
            FormConfig.model_validate({"elements": [], "appearance": {"colors": {"primary": "javascript:x"}}})
