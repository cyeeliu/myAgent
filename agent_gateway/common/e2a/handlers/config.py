"""Config handlers: config.get, config.set, config.save_all, config.validate_model."""
from __future__ import annotations

from agent_core import agents_flat_to_structured, write_agents_config

from agent_core import model_config

from ...schema.agent import AgentResponse
from ...schema.message import ReqMethod
from ..dispatcher import handler, HandlerContext
from ..helpers import config_get, config_set


@handler(ReqMethod.CONFIG_GET)
async def config_get_handler(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload=config_get())


@handler(ReqMethod.CONFIG_SET)
async def config_set_handler(req, ctx: HandlerContext):
    updates = {k: v for k, v in req.params.items() if k != "session_id"}
    resp = config_set(updates)
    if any(k.startswith("agent_name_") or k.startswith("agent_") and k.endswith("_name")
           or k.startswith("team_") or k.startswith("team_name_") for k in updates):
        try:
            agents, team = agents_flat_to_structured(updates)
            if agents or team:
                write_agents_config(agents, team)
                resp.setdefault("updated", [])
                if agents:
                    resp["updated"].append("agents")
                if team:
                    resp["updated"].append("team")
        except Exception:
            pass
    return AgentResponse(req.request_id, payload=resp)


@handler(ReqMethod.CONFIG_SAVE_ALL)
async def config_save_all(req, ctx: HandlerContext):
    params = req.params
    updated: list[str] = []
    models = params.get("models")
    if isinstance(models, list):
        model_config.write_models(models)
        updated.append("models")
    cfg_updates = params.get("config")
    if isinstance(cfg_updates, dict) and cfg_updates:
        r = config_set({k: v for k, v in cfg_updates.items() if k != "session_id"})
        if r.get("updated"):
            updated.extend(r["updated"])
    agents_payload = params.get("agents")
    team_payload = params.get("team")
    if agents_payload is not None or team_payload is not None:
        try:
            write_agents_config(agents_payload, team_payload)
            updated.append("agents")
        except Exception:
            pass
    return AgentResponse(req.request_id, payload={
        "updated": updated,
        "applied_without_restart": True,
    })


@handler(ReqMethod.CONFIG_VALIDATE_MODEL)
async def config_validate_model(req, ctx: HandlerContext):
    params = req.params
    saved = model_config.get_config()
    api_base = (params.get("api_base") or "").strip() or saved.get("base_url") or ""
    raw_key = (params.get("api_key") or "").strip()
    if not raw_key or "***" in raw_key:
        api_key = saved.get("api_key") or ""
    else:
        api_key = raw_key
    model_id = (params.get("model") or params.get("model_name") or "").strip() \
        or saved.get("model_id") or ""
    if not api_base or not api_key or not model_id:
        return AgentResponse(req.request_id, ok=False,
                             error="api_base, api_key and model are required")
    try:
        from openai import OpenAI
        client = OpenAI(base_url=api_base, api_key=api_key, timeout=30)
        listed = [m_obj.id for m_obj in client.models.list().data]
    except Exception as exc:
        return AgentResponse(req.request_id, ok=False,
                             error=f"{type(exc).__name__}: {exc}")
    if not listed:
        return AgentResponse(req.request_id, ok=False,
                             error="endpoint returned no models; cannot verify model id")
    if model_id not in listed:
        sample = ", ".join(listed[:12])
        more = f" …(+{len(listed) - 12} more)" if len(listed) > 12 else ""
        return AgentResponse(req.request_id, ok=False,
                             error=f"model '{model_id}' not in endpoint's model list. "
                                   f"Available: {sample}{more}")
    return AgentResponse(req.request_id, payload={"ok": True, "model_id": model_id})
