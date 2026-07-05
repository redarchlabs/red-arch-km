"""Unit tests for the extraction dispatcher (method/extension routing).

The real OCR/vision calls are monkeypatched, so these run without the tesseract
or poppler binaries and without the openai package or network.
"""

from __future__ import annotations

import pytest
from worker.extract import router
from worker.extract.router import extract_text


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Replace the three extractors with sentinels that record their args."""
    calls: dict[str, object] = {}

    def _pdf(data: bytes) -> str:
        calls["called"] = "tesseract_pdf"
        calls["data"] = data
        return "PDF-OCR"

    def _image(data: bytes) -> str:
        calls["called"] = "tesseract_image"
        return "IMG-OCR"

    def _ai(data: bytes, filename: str, api_key: str) -> str:
        calls["called"] = "ai"
        calls["filename"] = filename
        calls["api_key"] = api_key
        return "AI-TEXT"

    monkeypatch.setattr(router.tesseract, "extract_pdf", _pdf)
    monkeypatch.setattr(router.tesseract, "extract_image", _image)
    monkeypatch.setattr(router.openai_vision, "extract", _ai)
    return calls


def test_txt_decoded_directly_no_ocr(spy: dict[str, object]) -> None:
    result = extract_text(b"hello world", "notes.txt", "ocr", None)
    assert result == "hello world"
    assert "called" not in spy  # no extractor invoked


def test_md_decoded_directly_even_with_ai_method(spy: dict[str, object]) -> None:
    result = extract_text(b"# Title", "readme.md", "ai", "sk-x")
    assert result == "# Title"
    assert "called" not in spy


def test_pdf_ocr_routes_to_tesseract(spy: dict[str, object]) -> None:
    assert extract_text(b"%PDF", "scan.pdf", "ocr", None) == "PDF-OCR"
    assert spy["called"] == "tesseract_pdf"


@pytest.mark.parametrize("filename", ["photo.png", "photo.JPG", "scan.tiff", "art.webp"])
def test_image_ocr_routes_to_tesseract(spy: dict[str, object], filename: str) -> None:
    assert extract_text(b"\x89PNG", filename, "ocr", None) == "IMG-OCR"
    assert spy["called"] == "tesseract_image"


def test_pdf_ai_routes_to_vision_with_key(spy: dict[str, object]) -> None:
    assert extract_text(b"%PDF", "scan.pdf", "ai", "sk-org-key") == "AI-TEXT"
    assert spy["called"] == "ai"
    assert spy["api_key"] == "sk-org-key"
    assert spy["filename"] == "scan.pdf"


def test_image_ai_routes_to_vision(spy: dict[str, object]) -> None:
    assert extract_text(b"\x89PNG", "photo.png", "ai", "sk-x") == "AI-TEXT"
    assert spy["called"] == "ai"


def test_unsupported_extension_raises(spy: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="Unsupported file type"):
        extract_text(b"data", "archive.zip", "ocr", None)


def test_ai_without_key_still_dispatches_and_delegates_key_check(spy: dict[str, object]) -> None:
    # Router passes an empty string through; the real vision extractor is what
    # enforces "key required" (covered separately). Here it's monkeypatched.
    assert extract_text(b"%PDF", "scan.pdf", "ai", None) == "AI-TEXT"
    assert spy["api_key"] == ""


def test_openai_vision_requires_key() -> None:
    """The real vision extractor rejects an empty key (no network involved)."""
    from worker.extract import openai_vision

    with pytest.raises(ValueError, match="OpenAI API key is required"):
        openai_vision.extract(b"%PDF", "scan.pdf", "")
