"""Unit tests for serving org assets, with storage stubbed.

The interesting behaviour is not the read — it is what an ANONYMOUS caller is
allowed to reach through a share link, and what the failures tell them. A share
link is forwardable, so it must not become a way to read, or to enumerate,
everything an org has ever uploaded.
"""

from __future__ import annotations

import uuid

import pytest
from api.routers import assets as router
from api.services.assets import asset_key
from fastapi import HTTPException

pytestmark = pytest.mark.unit

ORG = uuid.UUID("6d44fc59-e4fc-4618-94a2-6febcdc775b2")
BODY = b"solid\n"


class _Storage:
    """Records the key it was asked for; raises for anything it does not hold."""

    def __init__(self, held: dict[str, bytes]):
        self.held = held
        self.asked: list[str] = []

    def get_object(self, key: str) -> bytes:
        self.asked.append(key)
        if key not in self.held:
            raise FileNotFoundError(key)
        return self.held[key]


@pytest.fixture
def storage(monkeypatch):
    store = _Storage({asset_key(ORG, "public/ships/a.stl"): BODY, asset_key(ORG, "private/a.stl"): BODY})
    monkeypatch.setattr(router, "StorageClient", lambda _settings: store)
    return store


class TestPublicAsset:
    def test_serves_an_asset_under_the_public_prefix(self, storage):
        response = router.public_asset_response(None, ORG, "public/ships/a.stl")
        assert response.status_code == 200
        assert response.body == BODY
        assert response.media_type == "model/stl"

    def test_reads_from_the_orgs_own_prefix(self, storage):
        router.public_asset_response(None, ORG, "public/ships/a.stl")
        assert storage.asked == [f"{ORG}/assets/public/ships/a.stl"]

    def test_is_cacheable_by_shared_caches(self, storage):
        response = router.public_asset_response(None, ORG, "public/ships/a.stl")
        # Anonymous and identical for every visitor, unlike the authenticated
        # route, which must stay out of a shared cache.
        assert response.headers["cache-control"] == "public, max-age=3600"

    def test_a_private_asset_is_404_not_403(self, storage):
        """It exists, and the caller must not learn that. 403 would confirm the
        key, which is the enumeration the prefix rule exists to prevent."""
        with pytest.raises(HTTPException) as exc:
            router.public_asset_response(None, ORG, "private/a.stl")
        assert exc.value.status_code == 404
        assert storage.asked == []  # refused before it ever reached storage

    def test_a_missing_asset_is_404(self, storage):
        with pytest.raises(HTTPException) as exc:
            router.public_asset_response(None, ORG, "public/ships/nope.stl")
        assert exc.value.status_code == 404

    @pytest.mark.parametrize("path", ["public/../../etc/passwd", "public/%2e%2e%2fsecret.stl"])
    def test_traversal_is_rejected_before_storage(self, storage, path):
        with pytest.raises(HTTPException) as exc:
            router.public_asset_response(None, ORG, path)
        assert exc.value.status_code == 400
        assert storage.asked == []

    def test_an_unservable_type_is_rejected(self, storage):
        # Nothing may be stored as one kind of file and served as another; an
        # org-controlled .html served from the API origin would be stored XSS.
        with pytest.raises(HTTPException) as exc:
            router.public_asset_response(None, ORG, "public/evil.html")
        assert exc.value.status_code == 400
        assert storage.asked == []
