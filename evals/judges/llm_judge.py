"""evals.judges.llm_judge — LLM-as-judge for open-ended tasks."""
from __future__ import annotations

import json
from evals.judges.base import Judge, JudgeResult
from evals.collectors.trace_model import EvalTrace


class LLMJudge(Judge):
    """Use an independent LLM to score the agent's output.

    Records judge_model + prompt for reproducibility.
    Supports self-consistency (multiple runs, take majority).
    """

    def __init__(self, rubric: str, model: str | None = None,
                 weight: float = 1.0, consistency_runs: int = 1):
        self.rubric = rubric
        self.model = model
        self.weight = weight
        self.consistency_runs = consistency_runs

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
        agent_output = " ".join(text_parts)

        prompt = self._build_prompt(agent_output, task)

        # E-Q12: resolve the judge model with a clear precedence:
        #   explicit ctor arg > task["judge_model"] > $JUDGE_MODEL > $MODEL_ID > default
        judge_model = self._resolve_model(task)

        scores = []
        for _ in range(self.consistency_runs):
            score = self._call_judge(prompt, judge_model)
            if score is not None:
                scores.append(score)

        if not scores:
            return JudgeResult(
                score=0.0, passed=False,
                reasoning="LLM judge unavailable (no API key or error)",
                details={"rubric": self.rubric, "model": judge_model},
            )

        avg_score = sum(scores) / len(scores)
        return JudgeResult(
            score=avg_score, passed=avg_score >= 0.5,
            reasoning=f"LLM judge avg={avg_score:.2f} over {len(scores)} runs",
            details={
                "rubric": self.rubric, "judge_model": judge_model,
                "prompt": prompt[:500], "scores": scores,
            },
        )

    def _resolve_model(self, task: dict) -> str:
        """E-Q12: judge model precedence — ctor > task > env > default."""
        if self.model:
            return self.model
        task_model = task.get("judge_model")
        if task_model:
            return task_model
        import os
        env_judge = os.environ.get("JUDGE_MODEL")
        if env_judge:
            return env_judge
        return os.environ.get("MODEL_ID", "gpt-4o-mini")

        avg_score = sum(scores) / len(scores)
        return JudgeResult(
            score=avg_score, passed=avg_score >= 0.5,
            reasoning=f"LLM judge avg={avg_score:.2f} over {len(scores)} runs",
            details={
                "rubric": self.rubric, "judge_model": self.model or "default",
                "prompt": prompt[:500], "scores": scores,
            },
        )

    def _build_prompt(self, agent_output: str, task: dict) -> str:
        return (
            f"You are an evaluation judge. Score the agent's response on a scale of 0.0 to 1.0.\n\n"
            f"Task: {task.get('prompt', '')}\n\n"
            f"Rubric: {self.rubric}\n\n"
            f"Agent output:\n{agent_output[:2000]}\n\n"
            f"Respond with a JSON object: {{\"score\": <float>, \"reasoning\": \"<text>\"}}"
        )

    def _call_judge(self, prompt: str, model: str) -> float | None:
        try:
            from agent_core.adapter import chat_create
            response = chat_create(
                model=model,
                system="You are an evaluation judge. Output only JSON.",
                messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                tools=[], max_tokens=200, stream=False,
            )
            text = ""
            for b in (response.content if hasattr(response, "content") else []):
                if isinstance(b, dict) and b.get("type") == "text":
                    text += b.get("text", "")
            result = json.loads(text)
            return float(result.get("score", 0.0))
        except Exception:
            return None
