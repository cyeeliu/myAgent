"""Tests for LLMJudge model resolution precedence (E-Q12)."""
from __future__ import annotations

import os

import pytest

from evals.judges.llm_judge import LLMJudge


def test_ctor_model_wins(monkeypatch):
    monkeypatch.setenv("JUDGE_MODEL", "env-judge")
    monkeypatch.setenv("MODEL_ID", "env-agent")
    j = LLMJudge(rubric="r", model="ctor-model")
    assert j._resolve_model({"judge_model": "task-model"}) == "ctor-model"


def test_task_model_beats_env(monkeypatch):
    monkeypatch.setenv("JUDGE_MODEL", "env-judge")
    monkeypatch.setenv("MODEL_ID", "env-agent")
    j = LLMJudge(rubric="r")
    assert j._resolve_model({"judge_model": "task-model"}) == "task-model"


def test_env_judge_model_beats_model_id(monkeypatch):
    monkeypatch.setenv("JUDGE_MODEL", "env-judge")
    monkeypatch.setenv("MODEL_ID", "env-agent")
    j = LLMJudge(rubric="r")
    assert j._resolve_model({}) == "env-judge"


def test_falls_back_to_model_id_then_default(monkeypatch):
    monkeypatch.delenv("JUDGE_MODEL", raising=False)
    monkeypatch.setenv("MODEL_ID", "env-agent")
    j = LLMJudge(rubric="r")
    assert j._resolve_model({}) == "env-agent"


def test_final_default_gpt_4o_mini(monkeypatch):
    monkeypatch.delenv("JUDGE_MODEL", raising=False)
    monkeypatch.delenv("MODEL_ID", raising=False)
    j = LLMJudge(rubric="r")
    assert j._resolve_model({}) == "gpt-4o-mini"
