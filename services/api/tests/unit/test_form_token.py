"""Unit tests for the public form token helper."""

from __future__ import annotations

from api.services import form_token


def test_generate_token_returns_raw_and_matching_hash() -> None:
    raw, token_hash = form_token.generate_token()
    assert raw and token_hash
    assert token_hash == form_token.hash_token(raw)
    assert raw != token_hash  # the raw token is never equal to its stored hash


def test_tokens_are_unique() -> None:
    tokens = {form_token.generate_token()[0] for _ in range(100)}
    assert len(tokens) == 100


def test_hash_is_stable_and_hex() -> None:
    h = form_token.hash_token("abc")
    assert h == form_token.hash_token("abc")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)
