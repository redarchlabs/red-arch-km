"""Text extraction (OCR / AI vision) for uploaded document originals."""

from worker.extract.router import extract_text

__all__ = ["extract_text"]
