"""evals.judges.composite_judge — Weighted combination of multiple judges."""
from __future__ import annotations

from evals.judges.base import Judge, JudgeResult
from evals.collectors.trace_model import EvalTrace


class CompositeJudge(Judge):
    """Combine multiple judges with weights."""

    def __init__(self, components: list[Judge], weights: list[float] | None = None):
        self.components = components
        if weights is None:
            # Equal weights
            n = len(components)
            self.weights = [1.0 / n] * n if n else []
        else:
            total = sum(weights) or 1.0
            self.weights = [w / total for w in weights]

    def run(self, trace: EvalTrace, metrics: dict, task: dict) -> JudgeResult:
        results = []
        total_score = 0.0
        all_passed = True
        for judge, weight in zip(self.components, self.weights):
            r = judge.run(trace, metrics, task)
            results.append({"score": r.score, "weight": weight,
                            "reasoning": r.reasoning, "passed": r.passed})
            total_score += weight * r.score
            if not r.passed:
                all_passed = False

        return JudgeResult(
            score=total_score, passed=all_passed,
            reasoning=f"composite score={total_score:.2f} from {len(results)} judges",
            details={"components": results},
        )
