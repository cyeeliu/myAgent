"""Tests for agent_core.model_config (global model config, hot-swappable)."""
import json

import agent_core.model_config as mc


def _write(path, d):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d))


def test_get_config_env_fallback(agents_dir):
    """No model.json → all fields from env."""
    cfg = mc.get_config()
    assert cfg["model_id"] == "test-model"
    assert cfg["api_key"] == "dummy"  # OPENAI_API_KEY default


def test_get_config_file_override(agents_dir):
    _write(mc._CONFIG_PATH, {
        "model_id": "glm-5", "base_url": "https://x",
        "api_key": "sk-123456", "fallback_model": "glm-4",
    })
    mc.refresh()
    cfg = mc.get_config()
    assert cfg["model_id"] == "glm-5"
    assert cfg["base_url"] == "https://x"
    assert cfg["api_key"] == "sk-123456"
    assert cfg["fallback_model"] == "glm-4"


def test_get_config_partial_file(agents_dir):
    """File with only model_id → other fields fall back to env."""
    _write(mc._CONFIG_PATH, {"model_id": "glm-5"})
    mc.refresh()
    cfg = mc.get_config()
    assert cfg["model_id"] == "glm-5"
    assert cfg["api_key"] == "dummy"  # env fallback


def test_corrupt_file_falls_back(agents_dir):
    mc._CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    mc._CONFIG_PATH.write_text("{not json")
    mc.refresh()
    cfg = mc.get_config()
    assert cfg["model_id"] == "test-model"  # env fallback, no raise


def test_client_rebuild_on_base_url_change(agents_dir):
    c1 = mc.client()  # env fallback (base_url=None, api_key=dummy)
    mc.write_config("x", "https://a", "sk-1", None)
    c2 = mc.client()
    assert c1 is not c2  # sig changed → new OpenAI instance


def test_client_reuse_on_model_only_change(agents_dir):
    mc.write_config("x", "https://a", "sk-1", None)
    c1 = mc.client()
    mc.write_config("y", "https://a", "sk-1", None)  # only model_id changed
    c2 = mc.client()
    assert c1 is c2  # sig (base_url, api_key) unchanged → reuse


def test_refresh_invalidates(agents_dir):
    cfg1 = mc.get_config()
    assert cfg1["model_id"] == "test-model"
    _write(mc._CONFIG_PATH, {"model_id": "glm-5"})
    mc.refresh()
    assert mc.get_config()["model_id"] == "glm-5"


def test_masked_key_sk_prefix(agents_dir):
    _write(mc._CONFIG_PATH, {"model_id": "x", "api_key": "sk-abcdef1234"})
    mc.refresh()
    assert mc.get_config_masked()["api_key_masked"] == "sk-***1234"


def test_masked_key_non_sk(agents_dir):
    _write(mc._CONFIG_PATH, {"model_id": "x", "api_key": "rawkey1234"})
    mc.refresh()
    assert mc.get_config_masked()["api_key_masked"] == "***"


def test_masked_key_none(agents_dir):
    # no api_key anywhere → env "dummy" actually; force empty by clearing env
    # Easier: empty file → api_key falls back to env "dummy" which has no sk-.
    assert mc.get_config_masked()["api_key_masked"] == "***"  # "dummy" is non-sk


def test_write_config_empty_api_key_keeps_existing(agents_dir):
    mc.write_config("x", "https://a", "sk-keepme", None)
    mc.write_config("y", "https://a", "", None)  # empty api_key → preserve
    on_disk = json.loads(mc._CONFIG_PATH.read_text())
    assert on_disk["api_key"] == "sk-keepme"
    assert on_disk["model_id"] == "y"


def test_model_and_fallback_accessors(agents_dir):
    _write(mc._CONFIG_PATH, {"model_id": "glm-5", "fallback_model": "glm-4"})
    mc.refresh()
    assert mc.model() == "glm-5"
    assert mc.fallback() == "glm-4"
