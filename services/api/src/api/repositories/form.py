"""Repositories for intake forms.

``FormRepository`` / ``FormLinkRepository`` are org-scoped like the other tenant
repos. ``resolve_link_by_token_hash`` is the one deliberately *un*-scoped lookup:
the public path has no org context until the token identifies the link (and thus
the org), so it must run on the privileged connection before tenant scoping.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.form import Form, FormLink


class FormRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def list_all(self) -> list[Form]:
        result = await self._session.execute(
            select(Form).where(Form.org_id == self._org_id).order_by(Form.name)
        )
        return list(result.scalars().all())

    async def count(self) -> int:
        """Number of forms in this org (cheaper than materialising list_all)."""
        result = await self._session.execute(
            select(func.count()).select_from(Form).where(Form.org_id == self._org_id)
        )
        return int(result.scalar_one())

    async def get(self, form_id: uuid.UUID) -> Form | None:
        result = await self._session.execute(
            select(Form).where(Form.id == form_id, Form.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Form | None:
        result = await self._session.execute(
            select(Form).where(Form.slug == slug, Form.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def create(self, form: Form) -> Form:
        form.org_id = self._org_id
        self._session.add(form)
        await self._session.flush()
        return form

    async def delete(self, form: Form) -> None:
        await self._session.delete(form)
        await self._session.flush()


class FormLinkRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def list_for_form(self, form_id: uuid.UUID) -> list[FormLink]:
        result = await self._session.execute(
            select(FormLink)
            .where(FormLink.form_id == form_id, FormLink.org_id == self._org_id)
            .order_by(FormLink.created_at.desc())
        )
        return list(result.scalars().all())

    async def get(self, link_id: uuid.UUID) -> FormLink | None:
        result = await self._session.execute(
            select(FormLink).where(FormLink.id == link_id, FormLink.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def create(self, link: FormLink) -> FormLink:
        link.org_id = self._org_id
        self._session.add(link)
        await self._session.flush()
        return link


async def resolve_link_by_token_hash(session: AsyncSession, token_hash: str) -> FormLink | None:
    """Look up a link by token hash WITHOUT org scoping.

    Only safe on the privileged (BYPASSRLS) connection in the public path: the
    token is the credential, and it identifies the org. Callers must then scope
    all subsequent tenant work to ``link.org_id``.
    """
    result = await session.execute(select(FormLink).where(FormLink.token_hash == token_hash))
    return result.scalar_one_or_none()


def unusable_reason(link: FormLink, now: datetime) -> str | None:
    """Return a human reason the link can't be used, or ``None`` if it's usable.

    Named for its return contract: a truthy string means UNUSABLE (the reason),
    ``None`` means the link is fine to use.
    """
    if link.status == "submitted":
        return "This form has already been submitted."
    if link.status in ("expired", "revoked"):
        return "This form link is no longer active."
    if link.expires_at is not None and link.expires_at < now:
        return "This form link has expired."
    return None
