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
