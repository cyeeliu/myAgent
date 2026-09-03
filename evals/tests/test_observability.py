"""Unit tests for the evals observability subsystem.

Covers JsonLogFormatter, RedactingFilter, EvalEventEmitter, AlertEvaluator,
AlertRuleLoader, PrometheusFormatter, MetricsJsonFormatter, TraceTreeBuilder,
TrendService.
"""
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

os.environ.setdefault("MODEL_ID", "test-model")
os.environ.setdefault("OPENAI_API_KEY", "dummy")


# ── JsonLogFormatter ──

class TestJsonLogFormatter:
    def _make_record(self, msg="hello", level=logging.INFO, **extra):
        record = logging.LogRecord(
            name="test", level=level, pathname="", lineno=0,
            msg=msg, args=(), exc_info=None,
        )
        for k, v in extra.items():
            setattr(record, k, v)
        return record

    def test_standard_fields(self):
        from evals.observability.logging import JsonLogFormatter
        fmt = JsonLogFormatter()
        record = self._make_record()
        result = json.loads(fmt.format(record))
        assert "timestamp" in result
        assert result["level"] == "INFO"
        assert result["message"] == "hello"
        assert "event_type" in result
        assert "run_id" in result
        assert "task_id" in result
        assert "trace_id" in result
        assert "extra" in result

    def test_iso8601_timestamp(self):
        from evals.observability.logging import JsonLogFormatter
        fmt = JsonLogFormatter()
        record = self._make_record()
        result = json.loads(fmt.format(record))
        ts = result["timestamp"]
        assert ts.endswith("Z")
        assert "T" in ts

    def test_empty_fields_are_strings(self):
        from evals.observability.logging import JsonLogFormatter
        fmt = JsonLogFormatter()
        record = self._make_record()
        result = json.loads(fmt.format(record))
        assert result["event_type"] == ""
        assert result["run_id"] == ""

    def test_serialization_fallback(self):
        from evals.observability.logging import JsonLogFormatter
        fmt = JsonLogFormatter()
        record = self._make_record()
        record.extra_data = {"obj": object()}
        result = json.loads(fmt.format(record))
        assert "extra" in result


# ── RedactingFilter ──

class TestRedactingFilter:
    def test_api_key_redacted(self):
        from evals.observability.logging import RedactingFilter
        flt = RedactingFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="key is sk-abcdefgh123456", args=(), exc_info=None,
        )
        assert flt.filter(record)
        assert "sk-abcdefgh123456" not in record.getMessage()
        assert "***REDACTED***" in record.getMessage()

    def test_bearer_token_redacted(self):
        from evals.observability.logging import RedactingFilter
        flt = RedactingFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="auth: Bearer abc-def-ghi", args=(), exc_info=None,
        )
        assert flt.filter(record)
        assert "abc-def-ghi" not in record.getMessage()

    def test_sensitive_field_in_extra(self):
        from evals.observability.logging import RedactingFilter
        flt = RedactingFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="msg", args=(), exc_info=None,
        )
        record.extra_data = {"api_key": "secret123", "normal": "keep"}
        flt.filter(record)
        assert record.extra_data["api_key"] == "***REDACTED***"
        assert record.extra_data["normal"] == "keep"

    def test_non_sensitive_not_harmed(self):
        from evals.observability.logging import RedactingFilter
        flt = RedactingFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="normal message", args=(), exc_info=None,
        )
        assert flt.filter(record)
        assert "normal" in record.getMessage()


# ── EvalEventEmitter ──

class TestEvalEventEmitter:
    def test_seq_strictly_increasing(self):
        from evals.observability.event_stream import EvalEventEmitter
        emitter = EvalEventEmitter()
        emitter.emit_run_started("run1")
        emitter.emit_task_started("run1", "t1")
        emitter.emit_task_completed("run1", "t1")
        pipe = emitter.get_pipe("run1")
        frames = pipe.replay_since(0)
        seqs = [f["seq"] for f in frames]
        assert seqs == sorted(seqs)
        assert len(seqs) == 3
        assert len(set(seqs)) == 3

    def test_all_event_types(self):
        from evals.observability.event_stream import EvalEventEmitter, EventType
        emitter = EvalEventEmitter()
        emitter.emit_run_started("r1")
        emitter.emit_task_started("r1", "t1")
        emitter.emit_task_completed("r1", "t1")
        emitter.emit_task_failed("r1", "t2", error="err")
        emitter.emit_progress("r1", "t1")
        emitter.emit_run_completed("r1")
        emitter.emit_run_cancelled("r2")
        pipe1 = emitter.get_pipe("r1")
        pipe2 = emitter.get_pipe("r2")
        frames1 = pipe1.replay_since(0)
        frames2 = pipe2.replay_since(0)
        kinds1 = {f["kind"] for f in frames1}
        kinds2 = {f["kind"] for f in frames2}
        assert "run_started" in kinds1
        assert "task_started" in kinds1
        assert "task_completed" in kinds1
        assert "task_failed" in kinds1
        assert "progress" in kinds1
        assert "run_completed" in kinds1
        assert "run_cancelled" in kinds2

    def test_event_truncation(self):
        from evals.observability.event_stream import RunEvent
        event = RunEvent(
            event_type="task_failed", run_id="r1",
            error="x" * 10000,
        )
        d = event.to_dict()
        assert len(json.dumps(d).encode()) <= 4096 or d.get("error_kind") == "truncated"


# ── AlertEvaluator ──

class TestAlertEvaluator:
    def _make_rules(self):
        from evals.observability.alerts import AlertRule
        return [
            AlertRule(name="low_pass", metric="pass_rate", operator="lt",
                      threshold=0.8, severity="critical"),
            AlertRule(name="high_cost", metric="total_cost", operator="gt",
                      threshold=10.0, severity="warning"),
        ]

    def test_violation_detected(self):
        from evals.observability.alerts import AlertEvaluator
        ev = AlertEvaluator()
        snapshot = {"pass_rate": {"value": 0.7}}
        events = ev.evaluate(snapshot, "r1", self._make_rules())
        assert any(e.rule_name == "low_pass" and e.state == "violated" for e in events)

    def test_no_violation(self):
        from evals.observability.alerts import AlertEvaluator
        ev = AlertEvaluator()
        snapshot = {"pass_rate": {"value": 0.9}}
        events = ev.evaluate(snapshot, "r1", self._make_rules())
        assert not any(e.rule_name == "low_pass" for e in events)

    def test_state_flip_dedup(self):
        from evals.observability.alerts import AlertEvaluator
        ev = AlertEvaluator()
        rules = self._make_rules()
        snap1 = {"pass_rate": {"value": 0.7}}
        snap2 = {"pass_rate": {"value": 0.7}}
        events1 = ev.evaluate(snap1, "r1", rules)
        events2 = ev.evaluate(snap2, "r1", rules)
        assert len(events1) == 1
        assert len(events2) == 0

    def test_recovery_event(self):
        from evals.observability.alerts import AlertEvaluator
        ev = AlertEvaluator()
        rules = self._make_rules()
        ev.evaluate({"pass_rate": {"value": 0.7}}, "r1", rules)
        events = ev.evaluate({"pass_rate": {"value": 0.9}}, "r1", rules)
        assert any(e.state == "recovered" for e in events)

    def test_missing_metric_skipped(self):
        from evals.observability.alerts import AlertEvaluator
        ev = AlertEvaluator()
        events = ev.evaluate({}, "r1", self._make_rules())
        assert len(events) == 0

    def test_non_numeric_skipped(self):
        from evals.observability.alerts import AlertEvaluator
        ev = AlertEvaluator()
        events = ev.evaluate({"pass_rate": {"value": "N/A"}}, "r1", self._make_rules())
        assert len(events) == 0


# ── AlertRuleLoader ──

class TestAlertRuleLoader:
    def test_json_loading(self, tmp_path):
        from evals.observability.alerts import AlertRuleLoader
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps({
            "rules": [
                {"name": "test1", "metric": "pass_rate", "operator": "lt",
                 "threshold": 0.8, "severity": "critical"},
                {"name": "test2", "metric": "cost", "operator": "gt",
                 "threshold": 5.0, "severity": "warning"},
            ]
        }))
        loader = AlertRuleLoader(rules_file)
        rules = loader.load()
        assert len(rules) == 2
        assert rules[0].name == "test1"
        assert rules[1].operator == "gt"

    def test_invalid_rule_skipped(self, tmp_path):
        from evals.observability.alerts import AlertRuleLoader
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps({
            "rules": [
                {"name": "bad_op", "metric": "pass_rate", "operator": "invalid",
                 "threshold": 0.8},
                {"name": "good", "metric": "pass_rate", "operator": "lt",
                 "threshold": 0.8},
            ]
        }))
        loader = AlertRuleLoader(rules_file)
        rules = loader.load()
        assert len(rules) == 1
        assert rules[0].name == "good"

    def test_hot_reload(self, tmp_path):
        from evals.observability.alerts import AlertRuleLoader
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps({"rules": []}))
        loader = AlertRuleLoader(rules_file)
        assert len(loader.load()) == 0
        time.sleep(0.01)
        rules_file.write_text(json.dumps({
            "rules": [{"name": "r1", "metric": "m", "operator": "gt", "threshold": 1}]
        }))
        rules = loader.load()
        assert len(rules) == 1


# ── PrometheusFormatter ──

class TestPrometheusFormatter:
    def test_format(self):
        from evals.observability.metrics_exporter import PrometheusFormatter
        snapshot = {
            "pass.rate": {"value": 0.9, "description": "Pass rate", "direction": "↑", "run_id": "r1"},
            "count": {"value": 10, "description": "Count", "direction": "↑", "run_id": "r1"},
        }
        text = PrometheusFormatter.format(snapshot)
        assert "# HELP pass_rate" in text
        assert "# TYPE pass_rate gauge" in text
        assert 'pass_rate{run_id="r1"}' in text
        assert "# HELP count" in text

    def test_non_numeric_skipped(self):
        from evals.observability.metrics_exporter import PrometheusFormatter
        snapshot = {"text_metric": {"value": "hello", "run_id": "r1"}}
        text = PrometheusFormatter.format(snapshot)
        assert text == ""

    def test_empty_snapshot(self):
        from evals.observability.metrics_exporter import PrometheusFormatter
        assert PrometheusFormatter.format({}) == ""


# ── MetricsJsonFormatter ──

class TestMetricsJsonFormatter:
    def test_format(self):
        from evals.observability.metrics_exporter import MetricsJsonFormatter
        snapshot = {"pass_rate": {"value": 0.9, "description": "Pass", "direction": "↑", "run_id": "r1"}}
        result = MetricsJsonFormatter.format(snapshot)
        assert result["pass_rate"]["value"] == 0.9
        assert result["pass_rate"]["description"] == "Pass"

    def test_empty(self):
        from evals.observability.metrics_exporter import MetricsJsonFormatter
        assert MetricsJsonFormatter.format({}) == {}


# ── TraceTreeBuilder ──

class TestTraceTreeBuilder:
    def test_empty_tree(self):
        from evals.observability.trace_viz import TraceTreeBuilder
        builder = TraceTreeBuilder()
        tree = builder.build("nonexistent", "nonexistent")
        assert "children" in tree
        assert tree["children"] == []


# ── TrendService ──

class TestTrendService:
    def test_degradation_direction(self):
        from evals.observability.trend import TrendService
        from evals.observability.router import ObservabilityContext
        svc = TrendService(ObservabilityContext(None, None))
        points = [
            {"run_id": "r1", "timestamp": 1, "pass_rate": 0.9, "changes": {}},
            {"run_id": "r2", "timestamp": 2, "pass_rate": 0.7, "changes": {}},
        ]
        meta = {"pass_rate": {"direction": "↑"}}
        for i in range(1, len(points)):
            for m in ["pass_rate"]:
                prev = points[i-1].get(m)
                curr = points[i].get(m)
                direction = meta.get(m, {}).get("direction", "↑")
                if curr < prev:
                    degraded = direction == "↑"
                else:
                    degraded = direction == "↓"
                points[i]["changes"][m] = "degraded" if degraded else "improved"
        assert points[1]["changes"]["pass_rate"] == "degraded"


# ── LiveScorecardCalculator ──

class TestLiveScorecardCalculator:
    def test_calculate(self):
        from evals.observability.dashboard import LiveScorecardCalculator
        calc = LiveScorecardCalculator()
        per_task = {
            "t1": {"status": "ok"},
            "t2": {"status": "error"},
            "t3": {"status": "ok"},
        }
        result = calc.calculate(per_task, [])
        assert result["completed"] == 3
        assert result["ok"] == 2
        assert result["error"] == 1
        assert result["pass_rate"] == pytest.approx(2/3)

    def test_empty(self):
        from evals.observability.dashboard import LiveScorecardCalculator
        calc = LiveScorecardCalculator()
        result = calc.calculate({}, [])
        assert result["pass_rate"] == 0.0
