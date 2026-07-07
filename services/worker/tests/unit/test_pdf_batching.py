"""Batched PDF rendering (worker.extract._pdf.iter_pdf_pages).

Proves the helper renders in bounded windows, respects the page cap, frees each
page bitmap, and terminates on a short batch when the page count is unknown —
the behaviour that lets very large documents OCR without holding every page
bitmap in memory at once.
"""

from __future__ import annotations

import pytest

from worker.extract import _pdf


class _FakeImage:
    def __init__(self, page: int) -> None:
        self.page = page
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _fake_convert(total_pages: int, calls: list[tuple[int, int]]):
    """convert_from_bytes stub: a document with ``total_pages`` real pages."""

    def _convert(_data: bytes, *, first_page: int, last_page: int) -> list[_FakeImage]:
        calls.append((first_page, last_page))
        return [_FakeImage(p) for p in range(first_page, min(last_page, total_pages) + 1)]

    return _convert


def test_renders_in_bounded_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(_pdf, "convert_from_bytes", _fake_convert(5, calls))
    monkeypatch.setattr(_pdf, "pdfinfo_from_bytes", lambda _d: {"Pages": 5})

    pages = list(_pdf.iter_pdf_pages(b"x", max_pages=100, batch_size=2))

    assert [p for p, _ in pages] == [1, 2, 3, 4, 5]
    # Never rendered more than batch_size pages in one call.
    assert calls == [(1, 2), (3, 4), (5, 5)]
    assert all(img.closed for _, img in pages)  # each bitmap freed after yield


def test_respects_max_pages_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(_pdf, "convert_from_bytes", _fake_convert(10, calls))
    monkeypatch.setattr(_pdf, "pdfinfo_from_bytes", lambda _d: {"Pages": 10})

    pages = list(_pdf.iter_pdf_pages(b"x", max_pages=3, batch_size=5))

    assert [p for p, _ in pages] == [1, 2, 3]  # capped, overflow skipped
    assert calls == [(1, 3)]


def test_terminates_on_short_batch_when_count_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(_pdf, "convert_from_bytes", _fake_convert(3, calls))
    # pdfinfo failing → total unknown; must stop when a batch comes back short.
    monkeypatch.setattr(_pdf, "pdfinfo_from_bytes", lambda _d: (_ for _ in ()).throw(RuntimeError("no poppler")))

    pages = list(_pdf.iter_pdf_pages(b"x", max_pages=100, batch_size=10))

    assert [p for p, _ in pages] == [1, 2, 3]
    assert calls == [(1, 10)]  # single short batch, then stop


def test_batch_size_floored_at_one(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(_pdf, "convert_from_bytes", _fake_convert(2, calls))
    monkeypatch.setattr(_pdf, "pdfinfo_from_bytes", lambda _d: {"Pages": 2})

    pages = list(_pdf.iter_pdf_pages(b"x", max_pages=100, batch_size=0))

    assert [p for p, _ in pages] == [1, 2]
    assert calls == [(1, 1), (2, 2)]  # floored to 1 page per batch
