"""Model handlers: models.list, models.replace_all."""
from __future__ import annotations

from agent_core import model_config

from ...schema.agent import AgentResponse
from ...schema.message import ReqMethod
from ..dispatcher import handler, HandlerContext


@handler(ReqMethod.MODELS_LIST)
async def models_list(req, ctx: HandlerContext):
    models = model_config.get_models()
    active = model_config.get_config()["model_id"]
    return AgentResponse(req.request_id, payload={
        "models": models,
        "active_model": active,
    })


@handler(ReqMethod.MODELS_REPLACE_ALL)
async def models_replace_all(req, ctx: HandlerContext):
    models = req.params.get("models") or []
    if isinstance(models, list):
        model_config.write_models(models)
    return AgentResponse(req.request_id, payload={"ok": True, "applied_without_restart": True})
