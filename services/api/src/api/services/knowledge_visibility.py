"""Which folders and documents a set of access masks may see, or add to.

Used by the public ``/api/v1/knowledge`` routes for API keys scoped to dimension
assignments, where ``masks`` are the masks of the key's own regions/roles/groups/
departments; ``None`` means org-wide (org keys), for which every check passes.

The rules mirror the member-facing UI, made strict where the UI is loose:

* **Folders** — visible when their *effective* view masks (own, or the nearest
  configured ancestor's) are empty or overlap ``masks``
  (:meth:`FolderRepository.list_visible_to_masks`).
* **Documents** — visible when filed in a visible folder AND, if the document has
  its own viewer override, that override's masks are empty or overlap ``masks``.
  Unfiled documents are not visible: the member list already hides them (they
  bypass folder permissions), and the internal ``GET /documents/{id}`` checks
  nothing at all, so a scoped key gets the stricter of the two.
* **Adding to a folder** — the folder must be visible, and if it (or its nearest
  ancestor with one) sets a contributor config, ``masks`` must overlap those
  contributor masks.

Masks match by exact integer overlap, the same way the vector store and the fact
graph match them.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.document import Document, Folder
from api.repositories.folder import FolderRepository


def _overlaps(resource_masks: list[int] | None, masks: list[int]) -> bool:
    """An empty resource mask list means unrestricted; otherwise any shared mask."""
    if not resource_masks:
        return True
    return bool(set(resource_masks) & set(masks))


async def visible_folder_ids(session: AsyncSession, org_id: uuid.UUID, masks: list[int] | None) -> list[uuid.UUID]:
    """Ids of every folder ``masks`` may see (all folders when ``masks`` is None)."""
    folders, _ = await FolderRepository(session, org_id).list_visible_to_masks(user_masks=masks)
    return [f.id for f in folders]


async def folder_visible(session: AsyncSession, org_id: uuid.UUID, folder: Folder, masks: list[int] | None) -> bool:
    if masks is None:
        return True
    effective = await FolderRepository(session, org_id).effective_view_masks(folder)
    return _overlaps(effective, masks)


async def document_visible(session: AsyncSession, org_id: uuid.UUID, doc: Document, masks: list[int] | None) -> bool:
    if masks is None:
        return True
    if doc.folder_id is None:
        return False
    folder = await FolderRepository(session, org_id).get(doc.folder_id)
    if folder is None or not await folder_visible(session, org_id, folder, masks):
        return False
    # The document's own viewer override, when it has one, must admit the masks too.
    return doc.viewer_permissions_config is None or _overlaps(doc.view_permission_masks, masks)


async def can_add_to_folder(session: AsyncSession, org_id: uuid.UUID, folder: Folder, masks: list[int] | None) -> bool:
    """Whether ``masks`` may add a document to ``folder`` (see module docstring)."""
    if masks is None:
        return True
    if not await folder_visible(session, org_id, folder, masks):
        return False
    contributors = await FolderRepository(session, org_id).effective_contributor_masks(folder)
    return _overlaps(contributors, masks)
