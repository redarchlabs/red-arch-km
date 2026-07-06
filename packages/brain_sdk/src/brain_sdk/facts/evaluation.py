"""Evaluation harness for the agentic fact engine.

Scores agent answers on three axes:

- **Groundedness** — did the answer avoid citing evidence it never gathered?
  (``AgentResult.unsupported_citations`` empty.)
- **Answer correctness** — do expected substrings appear, or (optionally) does an
  LLM judge deem the answer correct against a reference.
- **Tool usage** — were the expected tools exercised (e.g. an aggregative
  question should hit ``claim_query``).

``score_case`` is pure and unit-testable; ``FactEngineEvaluator`` runs an agent
over a suite and aggregates an :class:`EvalReport`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from brain_sdk.facts.agent import AgentContext, AgentResult, FactAgent
from brain_sdk.llm.protocol import LLMClient, LLMMessage

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One evaluation question and its expectations."""

    question: str
    expected_substrings: tuple[str, ...] = ()
    expected_tools: tuple[str, ...] = ()
    reference_answer: str | None = None


@dataclass(frozen=True, slots=True)
class CaseScore:
    question: str
    answer: str
    grounded: bool
    answer_match: bool
    tool_match: bool
    tools_used: tuple[str, ...]
    iterations: int
    judge_pass: bool | None = None

    @property
    def passed(self) -> bool:
        correctness = self.answer_match if self.judge_pass is None else self.judge_pass
        return self.grounded and correctness and self.tool_match


@dataclass(slots=True)
class EvalReport:
    scores: list[CaseScore] = field(default_factory=list)

    def _rate(self, predicate: str) -> float:
        if not self.scores:
            return 0.0
        hits = sum(1 for s in self.scores if getattr(s, predicate))
        return hits / len(self.scores)

    @property
    def groundedness_rate(self) -> float:
        return self._rate("grounded")

    @property
    def answer_accuracy(self) -> float:
        # Uses judge result when present, else substring match.
        if not self.scores:
            return 0.0
        hits = sum(1 for s in self.scores if (s.judge_pass if s.judge_pass is not None else s.answer_match))
        return hits / len(self.scores)

    @property
    def tool_rate(self) -> float:
        return self._rate("tool_match")

    @property
    def pass_rate(self) -> float:
        return self._rate("passed")

    def summary(self) -> dict[str, float | int]:
        return {
            "cases": len(self.scores),
            "pass_rate": round(self.pass_rate, 3),
            "groundedness": round(self.groundedness_rate, 3),
            "answer_accuracy": round(self.answer_accuracy, 3),
            "tool_rate": round(self.tool_rate, 3),
        }


def score_case(case: EvalCase, result: AgentResult, *, judge_pass: bool | None = None) -> CaseScore:
    """Pure structural scoring of one agent result against a case."""
    answer_lower = result.answer.lower()
    answer_match = all(sub.lower() in answer_lower for sub in case.expected_substrings)
    tools_used = tuple(str(e.get("tool", "")) for e in result.evidence)
    tool_match = set(case.expected_tools).issubset(set(tools_used))
    grounded = len(result.unsupported_citations) == 0
    return CaseScore(
        question=case.question,
        answer=result.answer,
        grounded=grounded,
        answer_match=answer_match,
        tool_match=tool_match,
        tools_used=tools_used,
        iterations=result.iterations,
        judge_pass=judge_pass,
    )


_JUDGE_SYSTEM = (
    "You judge whether a candidate answer is factually correct with respect to a "
    'reference answer. Reply with a JSON object {"correct": true|false}.'
)


class FactEngineEvaluator:
    """Runs an agent over an eval suite and aggregates a report."""

    def __init__(self, agent: FactAgent, *, judge: LLMClient | None = None) -> None:
        self._agent = agent
        self._judge = judge

    def run(self, tenant_id: str, cases: list[EvalCase], *, access_keys: tuple[int, ...] = ()) -> EvalReport:
        report = EvalReport()
        ctx = AgentContext(tenant_id=tenant_id, access_keys=access_keys)
        for case in cases:
            result = self._agent.run(case.question, ctx)
            judge_pass = self._judge_answer(case, result) if self._should_judge(case) else None
            report.scores.append(score_case(case, result, judge_pass=judge_pass))
        logger.info("Eval complete: %s", report.summary())
        return report

    def _should_judge(self, case: EvalCase) -> bool:
        return self._judge is not None and case.reference_answer is not None

    def _judge_answer(self, case: EvalCase, result: AgentResult) -> bool | None:
        if self._judge is None:
            return None
        import json

        user = (
            f"Question: {case.question}\n"
            f"Reference answer: {case.reference_answer}\n"
            f"Candidate answer: {result.answer}\n\nIs the candidate correct?"
        )
        try:
            raw = self._judge.complete(
                [LLMMessage("system", _JUDGE_SYSTEM), LLMMessage("user", user)],
                temperature=0.0,
                max_tokens=50,
                json_object=True,
            )
            return bool(json.loads(raw).get("correct"))
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning("Judge failed for %r: %s", case.question, exc)
            return None
