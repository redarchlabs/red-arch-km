"""The tag list a document carries in the knowledge index, and who may write it.

A document's chunks (Qdrant ``tags``) and its fact-graph Document node (Neo4j
``d.tags``) carry two kinds of tag in one list:

* the server-derived **folder tag** ``folder:<folder-id>`` — folder-limited search
  (a folder-limited API key, a per-folder chat) matches on it, so it IS folder
  membership as far as retrieval is concerned;
* the document's own **user tags** (``Tag.name``).

So a user tag spelled ``folder:<some-id>`` would forge membership of a folder the
document is not in. The prefix is reserved: tag names starting with it (any case,
after trimming) are refused wherever a tag name is accepted
(:func:`validate_tag_name`), and :func:`index_tags` drops any that predate the rule
so only the server-appended tag ever carries it.

Existing tags that already use the prefix can be listed (read-only) with::

    SELECT org_id, id, name FROM tags WHERE lower(btrim(name)) LIKE 'folder:%';
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

FOLDER_TAG_PREFIX = "folder:"


def is_reserved_tag_name(name: str) -> bool:
    """Whether ``name`` uses the server-only ``folder:`` prefix (any case, trimmed)."""
    return name.strip().casefold().startswith(FOLDER_TAG_PREFIX)


def validate_tag_name(name: str) -> str:
    """Raise ``ValueError`` for a tag name using the reserved prefix; else return it."""
    if is_reserved_tag_name(name):
        msg = f"Tag names may not start with '{FOLDER_TAG_PREFIX}' (reserved for folder membership)"
        raise ValueError(msg)
    return name


def folder_tag(folder_id: uuid.UUID | str) -> str:
    """The index tag that records membership of ``folder_id``."""
    return f"{FOLDER_TAG_PREFIX}{folder_id}"


def index_tags(user_tags: Iterable[str], folder_id: uuid.UUID | str | None) -> list[str]:
    """The tag list to index a document with: its user tags minus any using the
    reserved prefix, then its folder tag (when filed)."""
    tags = [name for name in user_tags if not is_reserved_tag_name(name)]
    if folder_id is not None:
        tags.append(folder_tag(folder_id))
    return tags
