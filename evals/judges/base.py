"""evals.judges.base — Judge base class and JudgeResult."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evals.collectors.trace_model import EvalTrace


@dataclass
class JudgeResult:
    """Result of judging a single task."""
    score: float = 0.0          # 0..1
    passed: bool = False
    reasoning: str = ""
    details: dict = field(default_factory=dict)


class Judge:
    """Base class for all judges."""
    def run(self, trace: EvalTrace, metrics: dict, task: dict) -> JudgeResult:
        raise NotImplementedError


def build_judge(spec: dict) -> Judge:
    """Factory: build a judge from a task's judge spec dict."""
    jtype = spec.get("type", "rule")
    if jtype == "rule":
        from evals.judges.rule_judge import RuleJudge
        return RuleJudge(spec.get("rules", []))
    elif jtype == "trajectory":
        from evals.judges.trajectory_judge import TrajectoryJudge
        return TrajectoryJudge(spec.get("expected_sequence", []),
                               spec.get("weight", 1.0))
    elif jtype == "reference":
        from evals.judges.reference_judge import ReferenceJudge
        return ReferenceJudge(spec.get("reference", ""),
                              spec.get("weight", 1.0))
    elif jtype == "llm_judge":
        from evals.judges.llm_judge import LLMJudge
        return LLMJudge(spec.get("rubric", ""),
                        spec.get("model", None),
                        spec.get("weight", 1.0),
                        spec.get("consistency_runs", 1))
    elif jtype == "composite":
        from evals.judges.composite_judge import CompositeJudge
        components = [build_judge(c) for c in spec.get("components", [])]
        return CompositeJudge(components)
    else:
        raise ValueError(f"unknown judge type: {jtype}")
