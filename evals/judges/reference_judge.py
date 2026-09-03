"""evals.judges.reference_judge — Reference output matching."""
from __future__ import annotations

from difflib import SequenceMatcher
from evals.judges.base import Judge, JudgeResult
from evals.collectors.trace_model import EvalTrace


class ReferenceJudge(Judge):
    """Compare agent output with a reference answer."""

    def __init__(self, reference: str, weight: float = 1.0):
        self.reference = reference
        self.weight = weight

    def run(self, trace: EvalTrace, metrics: dict, task: dict) -> JudgeResult:
        # Extract final assistant text
        text_parts = []
        for msg in reversed(trace.record):
            if msg.get("role") == "assistant":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "text":
                            text_parts.append(b.get("text", ""))
                elif isinstance(content, str):
                    text_parts.append(content)
                break
        actual = " ".join(text_parts)

        # Exact match
        if actual.strip() == self.reference.strip():
            return JudgeResult(score=1.0, passed=True, reasoning="exact match")

        # Fuzzy match
        ratio = SequenceMatcher(None, self.reference.lower(), actual.lower()).ratio()
        return JudgeResult(
            score=ratio, passed=ratio >= 0.7,
            reasoning=f"fuzzy match ratio={ratio:.2f}",
            details={"reference": self.reference[:200], "actual": actual[:200]},
        )
