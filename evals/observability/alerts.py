"""evals.observability.alerts — alert threshold subsystem.

AlertRule, AlertRuleLoader, AlertEvaluator, AlertStore, GET /eval/obs/alerts.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Query, HTTPException

from evals.observability import config
from evals.observability.router import ObservabilityContext


class Operator(str, Enum):
    GT = "gt"
    LT = "lt"
    EQ = "eq"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class AlertRule:
    name: str
    metric: str
    operator: str = "gt"
    threshold: float = 0.0
    severity: str = "warning"
    enabled: bool = True


@dataclass
class AlertEvent:
    rule_name: str
    run_id: str
    metric: str
    current_value: float = 0.0
    threshold: float = 0.0
    severity: str = "warning"
    triggered_at: float = field(default_factory=time.time)
    state: str = "violated"


class AlertRuleLoader:
    """Load alert rules from YAML/JSON with mtime-based hot reload."""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or config.EVAL_ALERT_RULES_PATH
        self._mtime: float = 0.0
        self._rules: list[AlertRule] = []
        self._lock = threading.Lock()

    def load(self) -> list[AlertRule]:
        with self._lock:
            try:
                if not self._path.exists():
                    return self._rules
                m = self._path.stat().st_mtime
                if m == self._mtime and self._rules:
                    return self._rules
                self._mtime = m
                self._rules = self._parse(self._path)
            except Exception:
                pass
            return self._rules

    def _parse(self, path: Path) -> list[AlertRule]:
        rules: list[AlertRule] = []
        try:
            if path.suffix in (".yaml", ".yml"):
                import yaml
                data = yaml.safe_load(path.read_text()) or {}
            else:
                import json
                data = json.loads(path.read_text())
        except Exception:
            return rules
        for item in data.get("rules", []):
            try:
                op = item.get("operator", "gt")
                sev = item.get("severity", "warning")
                if op not in ("gt", "lt", "eq"):
                    continue
                if sev not in ("info", "warning", "critical"):
                    continue
                rules.append(AlertRule(
                    name=item["name"],
                    metric=item["metric"],
                    operator=op,
                    threshold=float(item.get("threshold", 0)),
                    severity=sev,
                    enabled=item.get("enabled", True),
                ))
            except (KeyError, ValueError, TypeError):
                continue
        return rules


class AlertEvaluator:
    """Evaluate alert rules against metric snapshots with state-flip dedup."""

    def __init__(self):
        self._state: dict[tuple[str, str], bool] = {}
        self._lock = threading.Lock()

    def evaluate(self, snapshot: dict, run_id: str, rules: list[AlertRule]) -> list[AlertEvent]:
        events: list[AlertEvent] = []
        for rule in rules:
            if not rule.enabled:
                continue
            info = snapshot.get(rule.metric)
            if not info:
                continue
            val = info.get("value")
            if not isinstance(val, (int, float)):
                continue
            violated = self._check(rule, val)
            key = (run_id, rule.name)
            with self._lock:
                prev = self._state.get(key, False)
                if violated != prev:
                    self._state[key] = violated
                    events.append(AlertEvent(
                        rule_name=rule.name, run_id=run_id, metric=rule.metric,
                        current_value=val, threshold=rule.threshold,
                        severity=rule.severity,
                        state="violated" if violated else "recovered",
                    ))
        return events

    def _check(self, rule: AlertRule, val: float) -> bool:
        if rule.operator == "gt":
            return val > rule.threshold
        if rule.operator == "lt":
            return val < rule.threshold
        return val == rule.threshold

    def cleanup(self, run_id: str):
        with self._lock:
            keys = [k for k in self._state if k[0] == run_id]
            for k in keys:
                del self._state[k]


class AlertStore:
    """In-memory alert event store."""

    def __init__(self):
        self._events: list[AlertEvent] = []
        self._lock = threading.Lock()

    def add(self, event: AlertEvent):
        with self._lock:
            self._events.append(event)

    def query(self, run_id: Optional[str] = None, severity: Optional[str] = None,
              since: Optional[float] = None, until: Optional[float] = None,
              limit: int = 100) -> list[dict]:
        with self._lock:
            result = []
            for e in reversed(self._events):
                if run_id and e.run_id != run_id:
                    continue
                if severity and e.severity != severity:
                    continue
                if since and e.triggered_at < since:
                    continue
                if until and e.triggered_at > until:
                    continue
                result.append({
                    "rule_name": e.rule_name, "run_id": e.run_id, "metric": e.metric,
                    "current_value": e.current_value, "threshold": e.threshold,
                    "severity": e.severity, "triggered_at": e.triggered_at,
                    "state": e.state,
                })
                if len(result) >= limit:
                    break
            return result


_alert_store = AlertStore()
_alert_evaluator = AlertEvaluator()
_rule_loader = AlertRuleLoader()


def evaluate_snapshot(snapshot: dict, run_id: str):
    try:
        rules = _rule_loader.load()
        events = _alert_evaluator.evaluate(snapshot, run_id, rules)
        for e in events:
            _alert_store.add(e)
    except Exception:
        pass


def create_router(ctx: ObservabilityContext) -> APIRouter:
    router = APIRouter()

    @router.get("/alerts")
    async def alerts(run_id: Optional[str] = Query(None),
                     severity: Optional[str] = Query(None),
                     since: Optional[float] = Query(None),
                     until: Optional[float] = Query(None),
                     limit: int = Query(100)):
        return _alert_store.query(run_id, severity, since, until, limit)

    return router
