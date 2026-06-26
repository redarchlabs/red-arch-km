"""Common schemas: pagination, error envelope, success response."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def make_page[TItem](items: list[TItem], total: int, params: PaginationParams) -> PaginatedResponse[TItem]:
    """Build a PaginatedResponse from a slice of items and their total count."""
    pages = (total + params.page_size - 1) // params.page_size if total else 0
    return PaginatedResponse[TItem](
        items=items,
        total=total,
        page=params.page,
        page_size=params.page_size,
        pages=pages,
    )


class PaginatedResponse(BaseModel, Generic[T]):  # noqa: UP046  # deferred: REDARCH-14 (PEP 695 generic rewrite)
    model_config = ConfigDict(from_attributes=True)

    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


class ErrorResponse(BaseModel):
    detail: str
    request_id: str | None = None


class SuccessResponse(BaseModel):
    message: str
    data: Any = None
