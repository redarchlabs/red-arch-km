"""Failure handling inside the v1 document write service (no database).

* Only the external_ref unique index maps to the "raced, retry" 409; any other
  integrity error is a real fault and propagates.
* An original stored before a commit that then fails is deleted, not orphaned.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from api.auth.api_key import ApiKeyPrincipal
from api.services import knowledge_write
from api.services.knowledge_write import KnowledgeDocumentInput, KnowledgeWriteError, KnowledgeWriteService
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.unit


class _Orig(Exception):
    def __init__(self, constraint: str) -> None:
        super().__init__(f'duplicate key value violates unique constraint "{constraint}"')
        self.constraint_name = constraint


def _service(storage: MagicMock, commit_error: Exception) -> KnowledgeWriteService:
    session = MagicMock()
    session.commit = AsyncMock(side_effect=commit_error)
    session.rollback = AsyncMock()
    principal = ApiKeyPrincipal(api_key_id=uuid.uuid4(), org_id=uuid.uuid4(), scopes=frozenset(), name="k")
    folder = SimpleNamespace(id=uuid.uuid4())
    doc = SimpleNamespace(
        id=uuid.uuid4(),
        document_key="dk",
        external_ref=None,
        content_hash=None,
        size_bytes=None,
        document_url=None,
        title="t",
        text=None,
        metadata_={},
        use_knowledge_graph=None,
    )
    docs = MagicMock()
    docs.create = AsyncMock(return_value=doc)
    docs.get_by_external_ref = AsyncMock(return_value=None)
    folders = MagicMock()
    folders.get = AsyncMock(return_value=folder)
    folders.effective_view_masks = AsyncMock(return_value=[])
    with (
        patch.object(knowledge_write, "DocumentRepository", return_value=docs),
        patch.object(knowledge_write, "FolderRepository", return_value=folders),
    ):
        svc = KnowledgeWriteService(session, principal, MagicMock())
    return svc


_FILE = KnowledgeDocumentInput(
    folder_id=uuid.uuid4(), title="t", content=b"x", filename="a.md", external_ref="ref", metadata=None
)


@pytest.fixture(autouse=True)
def _authorised():  # noqa: ANN202
    with (
        patch.object(knowledge_write, "folder_visible", AsyncMock(return_value=True)),
        patch.object(knowledge_write, "can_add_to_folder", AsyncMock(return_value=True)),
    ):
        yield


async def test_ref_race_is_409_and_cleans_up_the_object() -> None:
    storage = MagicMock()
    err = IntegrityError("INSERT", {}, _Orig("uq_doc_external_ref_per_folder"))
    svc = _service(storage, err)
    with patch.object(knowledge_write, "StorageClient", lambda _s: storage), pytest.raises(KnowledgeWriteError) as exc:
        await svc.upsert(_FILE)
    assert exc.value.status_code == 409
    stored_key = storage.put_object.call_args.args[0]
    storage.delete_object.assert_called_once_with(stored_key)


async def test_other_integrity_errors_propagate_and_clean_up() -> None:
    storage = MagicMock()
    err = IntegrityError("INSERT", {}, _Orig("uq_doc_key_per_org"))
    svc = _service(storage, err)
    with patch.object(knowledge_write, "StorageClient", lambda _s: storage), pytest.raises(IntegrityError):
        await svc.upsert(_FILE)
    storage.delete_object.assert_called_once()


async def test_any_commit_failure_cleans_up_the_object() -> None:
    storage = MagicMock()
    svc = _service(storage, RuntimeError("connection reset"))
    with patch.object(knowledge_write, "StorageClient", lambda _s: storage), pytest.raises(RuntimeError):
        await svc.upsert(_FILE)
    storage.delete_object.assert_called_once()
