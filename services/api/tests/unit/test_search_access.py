"""Unit tests for the search/KB permission-mask helpers.

``service_key_access_keys`` is the security boundary that gives an org API key
org-wide content visibility, so its contract (always ``None`` = unrestricted) is
worth pinning explicitly."""

from __future__ import annotations

import uuid

from api.services.search_access import folder_tags, service_key_access_keys


def test_service_key_access_is_org_wide() -> None:
    # None means "no per-user mask filtering" — org-wide access for a service key.
    assert service_key_access_keys() is None


def test_folder_tags_empty_is_none() -> None:
    assert folder_tags([]) is None


def test_folder_tags_maps_ids_to_tags() -> None:
    fid = uuid.uuid4()
    assert folder_tags([fid]) == [f"folder:{fid}"]


class TestApiKeyFolderScope:
    """``api_key_folder_scope``: folders only narrow, and an empty allowed set is
    reported as ``[]`` (callers must then not call brain-api, where ``[]`` means
    "no folder filter")."""

    @staticmethod
    def _principal(folder_ids: frozenset[uuid.UUID] | None, mode: str = "scoped"):  # noqa: ANN205
        from api.auth.api_key import ApiKeyPrincipal

        return ApiKeyPrincipal(
            api_key_id=uuid.uuid4(),
            org_id=uuid.uuid4(),
            scopes=frozenset(),
            name="k",
            access_mode=mode,
            masks=(0,) if mode == "scoped" else None,
            folder_ids=folder_ids,
        )

    def test_unlimited_key_passes_the_request_through(self) -> None:
        from api.services.search_access import api_key_folder_scope

        fid = uuid.uuid4()
        assert api_key_folder_scope(self._principal(None, "org"), []) is None
        assert api_key_folder_scope(self._principal(None, "org"), [fid, fid]) == [fid]
        assert api_key_folder_scope(self._principal(None), None) is None

    def test_limited_key_defaults_to_its_allowed_set(self) -> None:
        from api.services.search_access import api_key_folder_scope

        a, b = uuid.uuid4(), uuid.uuid4()
        assert sorted(api_key_folder_scope(self._principal(frozenset({a, b})), []) or [], key=str) == sorted(
            [a, b], key=str
        )

    def test_limited_key_subset_ok_outside_is_404(self) -> None:
        import pytest
        from api.services.search_access import api_key_folder_scope
        from fastapi import HTTPException

        a, b = uuid.uuid4(), uuid.uuid4()
        assert api_key_folder_scope(self._principal(frozenset({a, b})), [a]) == [a]
        with pytest.raises(HTTPException) as exc:
            api_key_folder_scope(self._principal(frozenset({a})), [a, b])
        assert exc.value.status_code == 404
        assert exc.value.detail == "folder not found"

    def test_empty_allowed_set_is_an_empty_list_not_none(self) -> None:
        from api.services.search_access import api_key_folder_scope

        assert api_key_folder_scope(self._principal(frozenset()), []) == []

    def test_more_than_the_cap_is_422(self) -> None:
        import pytest
        from api.services.search_access import MAX_FOLDER_TAGS, api_key_folder_scope
        from fastapi import HTTPException

        many = frozenset(uuid.uuid4() for _ in range(MAX_FOLDER_TAGS + 1))
        with pytest.raises(HTTPException) as exc:
            api_key_folder_scope(self._principal(many), [])
        assert exc.value.status_code == 422
        assert api_key_folder_scope(self._principal(frozenset(list(many)[:MAX_FOLDER_TAGS])), []) is not None

    def test_scoped_key_masks_never_none_or_empty(self) -> None:
        import pytest
        from api.services.search_access import api_key_access_keys
        from fastapi import HTTPException

        assert api_key_access_keys(self._principal(None, "org")) is None
        assert api_key_access_keys(self._principal(None)) == [0]
        broken = self._principal(None)
        object.__setattr__(broken, "masks", ())
        with pytest.raises(HTTPException):
            api_key_access_keys(broken)
