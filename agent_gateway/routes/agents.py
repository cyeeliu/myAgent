"""Agent definition routes: CRUD for ``.agents/<name>.json`` presets."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent_core import list_agents, save_agent, delete_agent
from agent_gateway.schemas import AgentCreate, AgentUpdate

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
async def list_agents_route():
    return list_agents()


@router.post("")
async def create_agent(body: AgentCreate):
    try:
        return save_agent(body.name, body.description, body.prompt,
                               body.model, body.tools)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{name}")
async def update_agent(name: str, body: AgentUpdate):
    try:
        return save_agent(name, body.description, body.prompt,
                               body.model, body.tools)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{name}")
async def delete_agent_route(name: str):
    try:
        ok = delete_agent(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="agent not found")
    return {"ok": True}
