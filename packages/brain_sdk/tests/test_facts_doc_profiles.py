"""Unit tests for document profiles + hybrid type detection + per-doc brief."""

from __future__ import annotations

import json

from brain_sdk.facts.doc_profiles import (
    GENERIC,
    PROFILE_REGISTRY,
    DocumentProfiler,
    classify_by_metadata,
)
from brain_sdk.facts.extraction import ClaimExtractor


class FakeLLM:
    """Records the last system prompt and replays a canned response."""

    def __init__(self, response: str = "{}") -> None:
        self._response = response
        self.system_prompt: str | None = None
        self.calls = 0

    @property
    def model(self) -> str:
        return "fake"

    def complete(self, messages, *, temperature=0.2, max_tokens=1024, json_object=False):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.system_prompt = next((m.content for m in messages if m.role == "system"), None)
        return self._response


class TestHeuristicClassification:
    def test_directory_from_title(self) -> None:
        assert classify_by_metadata(title="Employee Directory") == "directory"

    def test_contract_from_folder(self) -> None:
        assert classify_by_metadata(folder_path="Legal/Contracts") == "contract"

    def test_meeting_from_tags(self) -> None:
        assert classify_by_metadata(tags=("minutes", "q3")) == "meeting_notes"

    def test_policy_from_title(self) -> None:
        assert classify_by_metadata(title="Remote Work Policy") == "policy"

    def test_leading_space_keyword_matches_at_start(self) -> None:
        # " nda" must match a title that *starts* with the acronym.
        assert classify_by_metadata(title="NDA with Acme") == "contract"

    def test_inconclusive_returns_none(self) -> None:
        assert classify_by_metadata(title="Untitled note", folder_path="Misc") is None


class TestProfilerWithoutLLM:
    def test_heuristic_type_yields_template_no_brief(self) -> None:
        profiler = DocumentProfiler(llm=None)
        profile = profiler.profile(title="Company Directory")
        assert profile.doc_type == "directory"
        assert "holds_title" in profile.priority_predicates
        assert profile.central_entities == ()  # no brief without an LLM
        assert profile.key_points == ()

    def test_unknown_type_falls_back_to_generic(self) -> None:
        profiler = DocumentProfiler(llm=None)
        profile = profiler.profile(title="Random musings")
        assert profile.doc_type == GENERIC
        assert profile is PROFILE_REGISTRY[GENERIC]


class TestProfilerHybrid:
    def test_heuristic_wins_but_brief_still_enriches(self) -> None:
        llm = FakeLLM(
            json.dumps(
                {
                    "doc_type": "generic",  # LLM disagrees; heuristic should win on type
                    "central_entities": ["Shawn", "Acme"],
                    "key_points": ["Shawn is the CMO"],
                }
            )
        )
        profiler = DocumentProfiler(llm=llm)  # type: ignore[arg-type]
        profile = profiler.profile(title="Employee Directory", sample_text="Shawn — CMO")
        assert profile.doc_type == "directory"  # heuristic precedence
        assert profile.central_entities == ("Shawn", "Acme")
        assert profile.key_points == ("Shawn is the CMO",)
        assert llm.calls == 1  # brief ran

    def test_llm_classifies_when_heuristic_inconclusive(self) -> None:
        llm = FakeLLM(
            json.dumps(
                {"doc_type": "meeting_notes", "central_entities": ["Team"], "key_points": []}
            )
        )
        profiler = DocumentProfiler(llm=llm)  # type: ignore[arg-type]
        profile = profiler.profile(title="Sync", sample_text="We decided to ship Friday.")
        assert profile.doc_type == "meeting_notes"
        assert profile.central_entities == ("Team",)

    def test_no_sample_text_skips_brief(self) -> None:
        llm = FakeLLM("{}")
        profiler = DocumentProfiler(llm=llm)  # type: ignore[arg-type]
        profile = profiler.profile(title="Company Directory", sample_text="  ")
        assert profile.doc_type == "directory"
        assert llm.calls == 0  # nothing to brief over

    def test_malformed_brief_degrades_gracefully(self) -> None:
        llm = FakeLLM("not json")
        profiler = DocumentProfiler(llm=llm)  # type: ignore[arg-type]
        profile = profiler.profile(title="Company Directory", sample_text="rows...")
        assert profile.doc_type == "directory"
        assert profile.central_entities == ()


class TestExtractionConditioning:
    def _extractor(self, llm: FakeLLM) -> ClaimExtractor:
        return ClaimExtractor(llm)  # type: ignore[arg-type]

    def test_profile_injects_guidance_and_predicates(self) -> None:
        llm = FakeLLM(json.dumps({"claims": []}))
        profile = PROFILE_REGISTRY["directory"].with_brief(
            central_entities=("Shawn",), key_points=("Shawn is the CMO",)
        )
        self._extractor(llm).extract("Shawn — CMO", profile)
        assert llm.system_prompt is not None
        assert "directory" in llm.system_prompt
        assert "holds_title" in llm.system_prompt
        assert "Shawn" in llm.system_prompt
        assert "Shawn is the CMO" in llm.system_prompt

    def test_generic_profile_adds_no_suffix(self) -> None:
        llm = FakeLLM(json.dumps({"claims": []}))
        base = self._extractor(FakeLLM(json.dumps({"claims": []})))
        # Capture the un-conditioned prompt.
        base_llm = FakeLLM(json.dumps({"claims": []}))
        ClaimExtractor(base_llm).extract("hi")  # type: ignore[arg-type]
        self._extractor(llm).extract("hi", PROFILE_REGISTRY[GENERIC])
        assert llm.system_prompt == base_llm.system_prompt

    def test_no_profile_matches_unconditioned_prompt(self) -> None:
        a = FakeLLM(json.dumps({"claims": []}))
        b = FakeLLM(json.dumps({"claims": []}))
        ClaimExtractor(a).extract("hi")  # type: ignore[arg-type]
        ClaimExtractor(b).extract("hi", None)  # type: ignore[arg-type]
        assert a.system_prompt == b.system_prompt
