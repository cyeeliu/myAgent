"""evals.metrics.memory_metrics — Memory extraction quality (5.11)."""
from __future__ import annotations

from pathlib import Path
from evals.metrics.base import register_metric
from evals.collectors.trace_model import EvalTrace


@register_metric("memory_extraction", "memory", "Memory extraction count and quality", "↑")
def memory_extraction(trace: EvalTrace, task: dict) -> dict:
    extracted = [e["payload"] for e in trace.events if e["kind"] == "memory"]
    count = len(extracted)
    # Check ground truth if provided
    gt = task.get("memory_ground_truth", [])
    if gt and count:
        extracted_names = set()
        for e in extracted:
            written = e.get("extracted", [])
            for w in written if isinstance(written, list) else [written]:
                if isinstance(w, dict):
                    extracted_names.add(w.get("name", ""))
                elif isinstance(w, str):
                    extracted_names.add(w)
        gt_set = set(gt)
        tp = len(extracted_names & gt_set)
        precision = tp / len(extracted_names) if extracted_names else 0.0
        recall = tp / len(gt_set) if gt_set else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        return {"count": count, "precision": precision, "recall": recall, "f1": f1}
    return {"count": count, "precision": None, "recall": None, "f1": None}
