"""Knowledge/fact engine: canonical entities + reified, bi-temporal claims.

Public surface:

- :mod:`brain_sdk.facts.models` — ``Entity``, ``Claim``, ``Provenance``, enums.
- :mod:`brain_sdk.facts.predicates` — predicate ontology + normalisation.
- :mod:`brain_sdk.facts.reconciliation` — pure create/corroborate/supersede/contradict policy.
- :mod:`brain_sdk.facts.protocol` — ``FactStore`` interface.
- :mod:`brain_sdk.facts.neo4j_fact_store` — Neo4j implementation.
"""

from __future__ import annotations

from brain_sdk.facts.agent import AgentContext, AgentResult, FactAgent
from brain_sdk.facts.digest import DigestBuilder, detect_communities
from brain_sdk.facts.evaluation import (
    CaseScore,
    EvalCase,
    EvalReport,
    FactEngineEvaluator,
    score_case,
)
from brain_sdk.facts.extraction import ClaimCandidate, ClaimExtractor, EntityMention
from brain_sdk.facts.models import (
    Claim,
    ClaimStatus,
    Entity,
    ObjectType,
    Provenance,
    compute_dedup_key,
    compute_entity_id,
)
from brain_sdk.facts.pipeline import Chunk, FactIngestPipeline
from brain_sdk.facts.predicates import Cardinality, PredicateRegistry, PredicateSpec
from brain_sdk.facts.protocol import FactStore
from brain_sdk.facts.reconciliation import ReconcileAction, ReconcileResult, reconcile
from brain_sdk.facts.resolution import (
    EntityResolver,
    ResolutionAction,
    ResolutionDecision,
    ResolutionThresholds,
    decide_resolution,
    resolve_predicate,
)

__all__ = [
    "AgentContext",
    "AgentResult",
    "Cardinality",
    "CaseScore",
    "Chunk",
    "Claim",
    "ClaimCandidate",
    "ClaimExtractor",
    "ClaimStatus",
    "DigestBuilder",
    "Entity",
    "EntityMention",
    "EntityResolver",
    "EvalCase",
    "EvalReport",
    "FactAgent",
    "FactEngineEvaluator",
    "FactIngestPipeline",
    "FactStore",
    "ObjectType",
    "detect_communities",
    "score_case",
    "PredicateRegistry",
    "PredicateSpec",
    "Provenance",
    "ReconcileAction",
    "ReconcileResult",
    "ResolutionAction",
    "ResolutionDecision",
    "ResolutionThresholds",
    "compute_dedup_key",
    "compute_entity_id",
    "decide_resolution",
    "reconcile",
    "resolve_predicate",
]
