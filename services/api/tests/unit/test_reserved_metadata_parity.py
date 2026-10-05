"""The API's reserved metadata keys must equal the ones brain-api strips at ingest.

The API refuses (or, on bundle import, drops) caller metadata naming an
index-owned field; brain-api (Python and Go) strips the same fields at ingest so
a stored key can never override the index's own. Three copies of one list: this
test fails the moment one of them changes without the others.

The brain-api sources are read from the repository rather than imported, so the
check runs even where those services are not installed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from api.schemas.reserved_metadata import RESERVED_METADATA_KEYS


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "go.work").is_file() and (parent / "services").is_dir():
            return parent
    msg = "repository root (go.work) not found above this test"
    raise AssertionError(msg)


def _python_reserved_keys() -> frozenset[str]:
    source = _repo_root() / "services/brain_api/src/brain_api/services/ingest_service.py"
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.AnnAssign | ast.Assign):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            if any(isinstance(t, ast.Name) and t.id == "RESERVED_INGEST_METADATA_KEYS" for t in targets):
                assert node.value is not None
                return frozenset(ast.literal_eval(node.value.args[0]))  # type: ignore[attr-defined]
    msg = f"RESERVED_INGEST_METADATA_KEYS not found in {source}"
    raise AssertionError(msg)


def _go_reserved_keys() -> frozenset[str]:
    source = _repo_root() / "services/brain-api-go/internal/pipeline/metadata.go"
    text = source.read_text(encoding="utf-8")
    block = re.search(r"var reservedIngestMetadataKeys = map\[string\]struct\{\}\{(.*?)\n\}", text, re.S)
    assert block is not None, f"reservedIngestMetadataKeys not found in {source}"
    return frozenset(re.findall(r'"([^"]+)"\s*:\s*\{\}', block.group(1)))


def test_matches_brain_api_python() -> None:
    assert _python_reserved_keys() == RESERVED_METADATA_KEYS


def test_matches_brain_api_go() -> None:
    assert _go_reserved_keys() == RESERVED_METADATA_KEYS


def test_the_python_brain_api_runtime_value_matches_too() -> None:
    # Where brain-api is installed (the workspace / CI), compare the live value as well.
    ingest = pytest.importorskip("brain_api.services.ingest_service")
    assert ingest.RESERVED_INGEST_METADATA_KEYS == RESERVED_METADATA_KEYS
