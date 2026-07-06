"""Unit tests for embedding-based predicate resolution (no hand-maintained dict)."""

from __future__ import annotations

from brain_sdk.facts.predicates import PredicateRegistry
from brain_sdk.facts.resolution import PredicateResolver


class ConceptEmbedder:
    """Deterministic, collision-free fake: phrases sharing a 'concept' get an
    identical one-hot vector (cosine 1.0); distinct concepts are orthogonal.

    So 'CEO' and 'CEO of' both map to the 'ceo' concept and match, while an
    unrelated phrase gets its own dimension and matches nothing — exercising the
    resolver's semantic match WITHOUT any alias entry.
    """

    _DIM = 256

    def __init__(self) -> None:
        self._index: dict[str, int] = {}

    def _concept(self, text: str) -> str:
        t = text.lower()
        if "ceo" in t or "chief executive" in t:
            return "ceo"
        if "headquart" in t or "based in" in t or "located" in t or "hq" in t:
            return "hq"
        if "acquir" in t or "bought" in t:
            return "acquire"
        words = t.replace("_", " ").split()
        return words[0] if words else "x"

    def embed(self, text: str) -> list[float]:
        concept = self._concept(text)
        idx = self._index.setdefault(concept, len(self._index))
        vec = [0.0] * self._DIM
        vec[idx % self._DIM] = 1.0
        return vec

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    @property
    def dimension(self) -> int:
        return self._DIM


def _resolver(threshold: float = 0.6) -> PredicateResolver:
    return PredicateResolver(ConceptEmbedder(), PredicateRegistry(), threshold=threshold)  # type: ignore[arg-type]


class TestPredicateResolver:
    def test_exact_key_fast_path(self) -> None:
        assert _resolver().key("ceo_of") == "ceo_of"

    def test_known_alias_fast_path(self) -> None:
        assert _resolver().key("based in") == "headquartered_in"

    def test_unknown_phrasing_matches_by_embedding(self) -> None:
        # 'CEO' is NOT an alias of ceo_of — only embedding similarity connects them.
        assert _resolver().key("CEO") == "ceo_of"

    def test_another_unmapped_surface_form(self) -> None:
        # 'chief executive officer' shares the 'ceo' concept → ceo_of.
        assert _resolver().key("chief executive officer") == "ceo_of"

    def test_unrelated_predicate_stays_open_domain(self) -> None:
        # No canonical predicate is close → keep it as a new (slugged) predicate.
        assert _resolver().key("enjoys playing chess with") == "enjoys_playing_chess_with"

    def test_result_is_cached(self) -> None:
        resolver = _resolver()
        first = resolver.resolve("CEO")
        second = resolver.resolve("CEO")
        assert first is second  # cached identical object
