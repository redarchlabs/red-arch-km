"""Unit tests for the eval harness (score_case pure + evaluator with fake agent)."""

from __future__ import annotations

from brain_sdk.facts.agent import AgentResult
from brain_sdk.facts.evaluation import EvalCase, FactEngineEvaluator, score_case


def _result(**kw: object) -> AgentResult:
    base: dict[str, object] = {
        "answer": "Acme is headquartered in Paris [E1].",
        "citations": ["E1"],
        "evidence": [{"id": "E1", "tool": "claim_query"}],
        "iterations": 2,
        "unsupported_citations": [],
    }
    base.update(kw)
    return AgentResult(**base)  # type: ignore[arg-type]


class TestScoreCase:
    def test_all_pass(self) -> None:
        case = EvalCase("Where is Acme HQ?", expected_substrings=("Paris",), expected_tools=("claim_query",))
        score = score_case(case, _result())
        assert score.grounded and score.answer_match and score.tool_match
        assert score.passed

    def test_missing_substring_fails_correctness(self) -> None:
        case = EvalCase("q", expected_substrings=("London",))
        score = score_case(case, _result())
        assert not score.answer_match
        assert not score.passed

    def test_ungrounded_citation_fails(self) -> None:
        case = EvalCase("q", expected_substrings=("Paris",))
        score = score_case(case, _result(unsupported_citations=["E9"]))
        assert not score.grounded
        assert not score.passed

    def test_missing_expected_tool_fails(self) -> None:
        case = EvalCase("q", expected_substrings=("Paris",), expected_tools=("neighborhood",))
        score = score_case(case, _result())
        assert not score.tool_match
        assert not score.passed

    def test_judge_overrides_substring_correctness(self) -> None:
        case = EvalCase("q", expected_substrings=("London",))  # substring would fail
        score = score_case(case, _result(), judge_pass=True)
        assert score.passed  # judge says correct → passes despite substring miss


class FakeAgent:
    def __init__(self, result: AgentResult) -> None:
        self._result = result
        self.questions: list[str] = []

    def run(self, question, ctx, *, history=None):  # type: ignore[no-untyped-def]
        self.questions.append(question)
        return self._result


class TestEvaluator:
    def test_run_aggregates_report(self) -> None:
        agent = FakeAgent(_result())
        evaluator = FactEngineEvaluator(agent)  # type: ignore[arg-type]
        cases = [
            EvalCase("Where is Acme HQ?", expected_substrings=("Paris",), expected_tools=("claim_query",)),
            EvalCase("Trick", expected_substrings=("Berlin",)),  # will fail correctness
        ]
        report = evaluator.run("t1", cases)

        assert len(report.scores) == 2
        assert report.groundedness_rate == 1.0
        assert report.answer_accuracy == 0.5
        assert report.pass_rate == 0.5
        assert agent.questions == ["Where is Acme HQ?", "Trick"]

    def test_summary_shape(self) -> None:
        report = FactEngineEvaluator(FakeAgent(_result())).run(  # type: ignore[arg-type]
            "t1", [EvalCase("q", expected_substrings=("Paris",))]
        )
        summary = report.summary()
        assert summary["cases"] == 1
        assert summary["pass_rate"] == 1.0
