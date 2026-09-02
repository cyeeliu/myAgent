"""Gateway integration tests for /api/agents CRUD."""
from fastapi.testclient import TestClient
from agent_gateway.main import app

client = TestClient(app)


def test_list_agents_empty(agents_dir):
    r = client.get("/api/agents")
    assert r.status_code == 200
    assert r.json() == []


def test_create_and_list_agent(agents_dir):
    r = client.post("/api/agents", json={
        "name": "researcher", "description": "explore",
        "prompt": "You are a researcher.", "model": None,
        "tools": ["bash", "read_file"],
    })
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "researcher"

    r = client.get("/api/agents")
    names = [a["name"] for a in r.json()]
    assert "researcher" in names


def test_create_agent_invalid_name(agents_dir):
    r = client.post("/api/agents", json={
        "name": "../x", "description": "", "prompt": "", "model": None, "tools": [],
    })
    assert r.status_code == 400


def test_update_agent(agents_dir):
    client.post("/api/agents", json={
        "name": "x", "description": "", "prompt": "old", "model": None, "tools": [],
    })
    r = client.put("/api/agents/x", json={
        "description": "new desc", "prompt": "new prompt", "model": "glm-4", "tools": ["bash"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["description"] == "new desc"
    assert body["model"] == "glm-4"


def test_delete_agent(agents_dir):
    client.post("/api/agents", json={
        "name": "x", "description": "", "prompt": "", "model": None, "tools": [],
    })
    r = client.delete("/api/agents/x")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_delete_agent_missing(agents_dir):
    r = client.delete("/api/agents/nope")
    assert r.status_code == 404
