"""evals.judges.trajectory_judge — Tool sequence comparison."""
from __future__ import annotations

from difflib import SequenceMatcher
from evals.judges.base import Judge, JudgeResult
from evals.collectors.trace_model import EvalTrace


class TrajectoryJudge(Judge):
    """Compare actual tool call sequence with expected sequence."""

    def __init__(self, expected_sequence: list[str], weight: float = 1.0):
        self.expected = expected_sequence
        self.weight = weight

    def run(self, trace: EvalTrace, metrics: dict, task: dict) -> JudgeResult:
        actual = [tc.name for tc in trace.tool_calls]
        if not self.expected:
            return JudgeResult(score=1.0, passed=True, reasoning="no expected sequence")

        # Set overlap score
        used = set(actual)
        expected_set = set(self.expected)
        overlap = len(used & expected_set)
        set_score = overlap / len(expected_set) if expected_set else 1.0

        # Edit distance similarity (order-aware)
        sm = SequenceMatcher(None, self.expected, actual)
        seq_ratio = sm.ratio()

        # Combined: 0.5 * set_overlap + 0.5 * sequence_ratio
        score = 0.5 * set_score + 0.5 * seq_ratio
        return JudgeResult(
            score=score, passed=score >= 0.5,
            reasoning=f"set_overlap={set_score:.2f}, seq_ratio={seq_ratio:.2f}",
            details={"expected": self.expected, "actual": actual,
                     "set_score": set_score, "seq_ratio": seq_ratio},
        )
