"""Unit tests for org asset key handling.

Assets are addressed by a caller-supplied path that becomes part of an object
storage key, so path handling is a security boundary: a key that escapes the
org's own prefix reads another tenant's files. Everything here is about that,
plus the public-prefix rule that decides what an anonymous share link may read.
"""

from __future__ import annotations

import uuid

import pytest
from api.services.assets import (
    PUBLIC_PREFIX,
    AssetError,
    asset_key,
    is_public_path,
    normalize_asset_path,
)

ORG = uuid.UUID("6d44fc59-e4fc-4618-94a2-6febcdc775b2")


class TestNormalize:
    @pytest.mark.parametrize(
        "path",
        [
            "ships/TB-10426.stl",
            "public/ships/TB-10426.stl",
            "a.png",
            "deep/nested/path/model.glb",
            "with-dash_and_underscore.2.stl",
        ],
    )
    def test_accepts_a_plain_relative_path(self, path):
        assert normalize_asset_path(path) == path

    def test_strips_a_leading_slash(self):
        assert normalize_asset_path("/ships/a.stl") == "ships/a.stl"

    def test_collapses_duplicate_slashes(self):
        assert normalize_asset_path("ships//a.stl") == "ships/a.stl"

    @pytest.mark.parametrize(
        "path",
        [
            "../secrets.stl",
            "ships/../../other-org/a.stl",
            "ships/./a.stl",
            "..",
            "ships/..",
            "%2e%2e/a.stl",
            "..%2fa.stl",
        ],
    )
    def test_rejects_traversal(self, path):
        # The whole point: nothing may address a key outside the org's prefix.
        with pytest.raises(AssetError, match="path"):
            normalize_asset_path(path)

    @pytest.mark.parametrize(
        "path",
        ["", "   ", "/", "ships/", "a\\b.stl", "a\x00b.stl", "a\nb.stl"],
    )
    def test_rejects_malformed(self, path):
        with pytest.raises(AssetError):
            normalize_asset_path(path)

    def test_rejects_an_over_long_path(self):
        with pytest.raises(AssetError, match="too long"):
            normalize_asset_path("x" * 400 + ".stl")


class TestKey:
    def test_key_is_scoped_to_the_org(self):
        assert asset_key(ORG, "ships/a.stl") == f"{ORG}/assets/ships/a.stl"

    def test_key_normalizes_its_path(self):
        assert asset_key(ORG, "/ships//a.stl") == f"{ORG}/assets/ships/a.stl"

    def test_key_refuses_traversal(self):
        with pytest.raises(AssetError):
            asset_key(ORG, "../../etc/passwd")


class TestPublicPrefix:
    """An anonymous share link may read only assets deliberately placed under
    `public/`. Sharing a view must not turn into read access to everything the
    org has ever uploaded, and an explicit prefix is the same opt-in shape the
    view share itself uses."""

    def test_public_prefix_is_readable(self):
        assert is_public_path(f"{PUBLIC_PREFIX}/ships/a.stl") is True

    @pytest.mark.parametrize(
        "path",
        [
            "ships/a.stl",
            "private/a.stl",
            "publicish/a.stl",  # prefix match must be on the path SEGMENT
            "x/public/a.stl",
        ],
    )
    def test_everything_else_is_not(self, path):
        assert is_public_path(path) is False
