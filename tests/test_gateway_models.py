"""Gateway integration tests for /api/models (global model config)."""
import json

from fastapi.testclient import TestClient
from agent_gateway.main import app
import agent_core.model_config as mc

client = TestClient(app)


def test_get_models_env_fallback(agents_dir):
    r = client.get("/api/models")
    assert r.status_code == 200
    body = r.json()
    assert body["model_id"] == "test-model"
    assert "api_key_masked" in body
    assert "api_key" not in body  # raw key never exposed


def test_get_models_masks_key(agents_dir):
    mc.write_config("glm-5", "https://x", "sk-abcdef1234", "glm-4")
    r = client.get("/api/models")
    body = r.json()
    assert body["api_key_masked"] == "sk-***1234"
    assert body["model_id"] == "glm-5"
    assert body["fallback_model"] == "glm-4"


def test_update_models_writes_file(agents_dir):
    r = client.put("/api/models", json={
        "model_id": "glm-5", "base_url": "https://x",
        "api_key": "sk-newkey", "fallback_model": "glm-4",
    })
    assert r.status_code == 200, r.text
    on_disk = json.loads(mc._CONFIG_PATH.read_text())
    assert on_disk["model_id"] == "glm-5"
    assert on_disk["api_key"] == "sk-newkey"


def test_update_models_empty_api_key_keeps_existing(agents_dir):
    client.put("/api/models", json={
        "model_id": "a", "base_url": None, "api_key": "sk-keep", "fallback_model": None,
    })
    client.put("/api/models", json={
        "model_id": "b", "base_url": None, "api_key": "", "fallback_model": None,
    })
    on_disk = json.loads(mc._CONFIG_PATH.read_text())
    assert on_disk["api_key"] == "sk-keep"
    assert on_disk["model_id"] == "b"


def test_update_models_takes_effect(agents_dir):
    client.put("/api/models", json={
        "model_id": "glm-5", "base_url": None, "api_key": None, "fallback_model": None,
    })
    r = client.get("/api/models")
    assert r.json()["model_id"] == "glm-5"
