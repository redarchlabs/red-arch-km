"""Application-level encryption for secrets stored at rest.

Per-org third-party credentials (e.g. ``orgs.openai_api_key``) must not sit in
the database, backups, or internal API responses as plaintext. This module
provides symmetric encryption via Fernet (AES-128-CBC + HMAC-SHA256), keyed by a
deterministic 32-byte key derived from the configured ``ORG_ENCRYPTION_KEY``
secret.

Two design points:

* **Deterministic key derivation.** The Fernet key is ``urlsafe_b64encode`` of
  ``sha256(secret)`` so any process configured with the same ``ORG_ENCRYPTION_KEY``
  can decrypt — the API, the worker, and one-off migrations all agree without
  sharing a generated key file.
* **Legacy-plaintext tolerance.** ``decrypt_secret`` returns the input unchanged
  when it is not a valid Fernet token. Rows written before encryption existed
  (or manually inserted plaintext) therefore keep working instead of raising, so
  reads never 500 during the migration window.

Never log the plaintext value.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def _derive_fernet_key(secret: str) -> bytes:
    """Derive a stable urlsafe-base64 32-byte Fernet key from an arbitrary secret.

    Using a SHA-256 digest lets operators configure any string as
    ``ORG_ENCRYPTION_KEY`` (not just a pre-generated Fernet key) while still
    yielding the 32-byte key Fernet requires.
    """
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def get_fernet(secret: str) -> Fernet:
    """Return a Fernet instance for the given secret."""
    return Fernet(_derive_fernet_key(secret))


def encrypt_secret(plaintext: str, secret: str) -> str:
    """Encrypt ``plaintext`` and return an ASCII Fernet token."""
    return get_fernet(secret).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str, secret: str) -> str:
    """Decrypt a Fernet token, tolerating legacy plaintext.

    If ``value`` is not a valid Fernet token (e.g. a pre-encryption plaintext
    row) it is returned unchanged rather than raising, so reads stay resilient
    during/after the encryption rollout.
    """
    try:
        return get_fernet(secret).decrypt(value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return value
