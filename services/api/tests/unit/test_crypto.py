"""Unit tests for application-level secret encryption (services/crypto.py).

Pure crypto — no DB, no settings singleton.
"""

from __future__ import annotations

from api.services.crypto import decrypt_secret, encrypt_secret

SECRET = "unit-test-encryption-secret"  # noqa: S105 - test fixture, not a real credential


def test_round_trip() -> None:
    plaintext = "sk-live-abc123DEF456"
    token = encrypt_secret(plaintext, SECRET)
    assert decrypt_secret(token, SECRET) == plaintext


def test_ciphertext_is_not_plaintext() -> None:
    plaintext = "sk-super-secret-key"
    token = encrypt_secret(plaintext, SECRET)
    assert token != plaintext
    assert plaintext not in token


def test_encrypt_is_non_deterministic() -> None:
    # Fernet embeds a random IV + timestamp, so two encryptions differ but both
    # decrypt back to the same plaintext.
    plaintext = "sk-abc"
    a = encrypt_secret(plaintext, SECRET)
    b = encrypt_secret(plaintext, SECRET)
    assert a != b
    assert decrypt_secret(a, SECRET) == plaintext
    assert decrypt_secret(b, SECRET) == plaintext


def test_decrypt_tolerates_legacy_plaintext() -> None:
    # A pre-encryption plaintext value is not a valid Fernet token; decrypt must
    # return it unchanged rather than raising, so reads never 500.
    legacy = "sk-legacy-plaintext-value"
    assert decrypt_secret(legacy, SECRET) == legacy


def test_decrypt_tolerates_arbitrary_garbage() -> None:
    assert decrypt_secret("not-a-token!!!", SECRET) == "not-a-token!!!"
    assert decrypt_secret("", SECRET) == ""


def test_wrong_secret_does_not_return_plaintext() -> None:
    # Decrypting with the wrong secret is an InvalidToken; per the legacy-plaintext
    # policy the (still-ciphertext) input is returned as-is — never the plaintext.
    token = encrypt_secret("sk-abc", SECRET)
    result = decrypt_secret(token, "a-different-secret")
    assert result == token
    assert result != "sk-abc"
