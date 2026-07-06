"""Fact-ingestion orchestration: chunks → claims in the fact store.

Chains the three ingest stages built in earlier slices:

    extract candidates  →  resolve entities + predicates  →  reconcile + store

Idempotent by design: ``ingest_document`` purges the document's prior claims
before re-inserting, so re-processing the same document converges instead of
duplicating. (The brain-api layer adds a content-hash short-circuit so an
unchanged document is skipped entirely.)
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from brain_sdk.facts.extraction import ClaimCandidate, ClaimExtractor
from brain_sdk.facts.models import Claim, ObjectType, Provenance
from brain_sdk.facts.predicates import PredicateRegistry
from brain_sdk.facts.protocol import FactStore
from brain_sdk.facts.resolution import EntityResolver, resolve_predicate

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Chunk:
    """A source chunk: a stable id (for provenance) and its text."""

    chunk_id: str
    text: str


class FactIngestPipeline:
    """Orchestrates extraction → resolution → fact-store insertion for a document."""

    def __init__(
        self,
        extractor: ClaimExtractor,
        resolver: EntityResolver,
        store: FactStore,
        *,
        registry: PredicateRegistry | None = None,
        model_name: str = "claim-extractor",
    ) -> None:
        self._extractor = extractor
        self._resolver = resolver
        self._store = store
        self._registry = registry or PredicateRegistry()
        self._model_name = model_name

    def ingest_document(
        self,
        tenant_id: str,
        document_key: str,
        chunks: Sequence[Chunk],
        *,
        tags: tuple[str, ...] = (),
        access_keys: tuple[int, ...] = (),
        replace: bool = True,
    ) -> dict[str, int]:
        """Extract, resolve, and store all claims for one document.

        ``replace=True`` purges the document's existing claims first (idempotent
        re-ingest).
        """
        if replace:
            self._store.delete_by_document_key(tenant_id, document_key)

        claims: list[Claim] = []
        for chunk in chunks:
            for candidate in self._safe_extract(chunk):
                claim = self._to_claim(tenant_id, document_key, chunk.chunk_id, candidate, tags, access_keys)
                if claim is not None:
                    claims.append(claim)

        counts = self._store.insert_claims(tenant_id, claims)
        counts["claims_extracted"] = len(claims)
        logger.info("Fact ingest for %s (tenant %s): %s", document_key, tenant_id, counts)
        return counts

    def _safe_extract(self, chunk: Chunk) -> list[ClaimCandidate]:
        try:
            return self._extractor.extract(chunk.text)
        except Exception as exc:  # noqa: BLE001 - one bad chunk must not abort the doc
            logger.warning("Extraction failed for chunk %s: %s", chunk.chunk_id, exc)
            return []

    def _to_claim(
        self,
        tenant_id: str,
        document_key: str,
        chunk_id: str,
        candidate: ClaimCandidate,
        tags: tuple[str, ...],
        access_keys: tuple[int, ...],
    ) -> Claim | None:
        try:
            subject_id = self._resolver.resolve(tenant_id, candidate.subject.name, candidate.subject.type)
            predicate = resolve_predicate(self._registry, candidate.predicate)
            provenance = Provenance(
                document_key=document_key,
                chunk_id=chunk_id,
                text_span=candidate.text_span,
                extractor_model=self._model_name,
                confidence=candidate.confidence,
            )
            if candidate.object_is_entity and candidate.object_entity is not None:
                object_id = self._resolver.resolve(
                    tenant_id, candidate.object_entity.name, candidate.object_entity.type
                )
                return Claim(
                    tenant_id=tenant_id,
                    subject_id=subject_id,
                    predicate=predicate,
                    object_type=ObjectType.ENTITY,
                    object_id=object_id,
                    confidence=candidate.confidence,
                    access_keys=access_keys,
                    tags=tags,
                    provenance=(provenance,),
                )
            return Claim(
                tenant_id=tenant_id,
                subject_id=subject_id,
                predicate=predicate,
                object_type=candidate.object_value_type,
                object_value=candidate.object_value,
                confidence=candidate.confidence,
                access_keys=access_keys,
                tags=tags,
                provenance=(provenance,),
            )
        except Exception as exc:  # noqa: BLE001 - skip a claim we cannot resolve, keep the rest
            logger.warning("Failed to build claim from candidate %r: %s", candidate.predicate, exc)
            return None
