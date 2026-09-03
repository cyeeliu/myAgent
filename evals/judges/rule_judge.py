"""evals.judges.rule_judge — Rule-based judgment with atomic rules."""
from __future__ import annotations

import re
from pathlib import Path
from evals.judges.base import Judge, JudgeResult
from evals.collectors.trace_model import EvalTrace


class RuleJudge(Judge):
    """Judge based on a set of atomic rules. All must pass for score=1.0."""

    def __init__(self, rules: list[dict]):
        self.rules = rules

    def run(self, trace: EvalTrace, metrics: dict, task: dict) -> JudgeResult:
        results = []
        all_passed = True
        for rule in self.rules:
            kind = rule.get("kind", "")
            passed, detail = self._check_rule(kind, rule, trace, metrics, task)
            results.append({"kind": kind, "passed": passed, "detail": detail})
            if not passed:
                all_passed = False

        score = 1.0 if all_passed else 0.0
        return JudgeResult(
            score=score, passed=all_passed,
            reasoning=f"{'All' if all_passed else 'Some'} rules passed ({len(results)} total)",
            details={"rules": results},
        )

    def _check_rule(self, kind: str, rule: dict, trace: EvalTrace,
                    metrics: dict, task: dict) -> tuple[bool, str]:
        if kind == "tool_used":
            tool = rule.get("tool", "")
            used = any(tc.name == tool for tc in trace.tool_calls)
            return used, f"tool '{tool}' {'used' if used else 'not used'}"

        elif kind == "no_forbidden_tools":
            forbidden = set(task.get("forbidden_tools", []))
            used = set(tc.name for tc in trace.tool_calls)
            hit = used & forbidden
            return not hit, f"forbidden tools hit: {hit}" if hit else "no forbidden tools"

        elif kind == "file_exists":
            path = rule.get("path", "")
            exists = Path(path).exists()
            return exists, f"file '{path}' {'exists' if exists else 'missing'}"

        elif kind == "tests_pass":
            # Delegate to SWE-bench adapter or subprocess
            test_cmd = rule.get("command", "pytest")
            import subprocess
            try:
                r = subprocess.run(test_cmd, shell=True, capture_output=True, timeout=60)
                passed = r.returncode == 0
                return passed, f"tests {'passed' if passed else 'failed'} (rc={r.returncode})"
            except Exception as e:
                return False, f"test error: {e}"

        elif kind == "regex_in_output":
            pattern = rule.get("pattern", "")
            text = " ".join(
                str(b.get("text", "")) for msg in trace.record
                if msg.get("role") == "assistant"
                for b in (msg.get("content", []) if isinstance(msg.get("content"), list)
                          else [{"type": "text", "text": str(msg.get("content", ""))}])
                if isinstance(b, dict) and b.get("type") == "text"
            )
            matched = bool(re.search(pattern, text, re.IGNORECASE))
            return matched, f"pattern '{pattern}' {'matched' if matched else 'not found'}"

        elif kind == "no_error":
            has_error = any(e["kind"] == "error" for e in trace.events)
            return not has_error, "no errors" if not has_error else "errors present"

        elif kind == "max_turns":
            max_t = rule.get("max", 10)
            actual = len(trace.turns)
            return actual <= max_t, f"{actual}/{max_t} turns"

        elif kind == "done_within_turns":
            max_t = rule.get("max", 10)
            actual = len(trace.turns)
            has_done = any(e["kind"] == "done" for e in trace.events)
            return has_done and actual <= max_t, f"done={has_done}, {actual}/{max_t} turns"

        else:
            return False, f"unknown rule kind: {kind}"
