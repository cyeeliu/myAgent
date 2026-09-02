"""Skills handlers: list, get, toggle, install, uninstall, marketplace ops."""
from __future__ import annotations

import asyncio

from agent_core import scan_skills, get_skill, set_skill_enabled, uninstall_skill, install_skill, import_local_skill, import_upload_skill, list_marketplaces

from ...schema.agent import AgentResponse
from ...schema.message import ReqMethod
from ..dispatcher import handler, HandlerContext


def _mp():
    from .... import skill_marketplaces as mp
    return mp


@handler(ReqMethod.SKILLS_LIST)
async def skills_list(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload={"skills": scan_skills()})


@handler(ReqMethod.SKILLS_INSTALLED)
async def skills_installed(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload={"skills": scan_skills()})


@handler(ReqMethod.SKILLS_GET)
async def skills_get(req, ctx: HandlerContext):
    name = req.params.get("name", "")
    skill = get_skill(name) if name else None
    return AgentResponse(req.request_id, payload=skill or {})


@handler(ReqMethod.SKILLS_TOGGLE)
async def skills_toggle(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload=set_skill_enabled(
        req.params.get("name", ""), bool(req.params.get("enabled", True))))


@handler(ReqMethod.SKILLS_UNINSTALL)
async def skills_uninstall(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload=uninstall_skill(req.params.get("name", "")))


@handler(ReqMethod.SKILLS_INSTALL)
async def skills_install(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload=install_skill(
        req.params.get("spec", ""), bool(req.params.get("force", False))))


@handler(ReqMethod.SKILLS_IMPORT_LOCAL)
async def skills_import_local(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload=import_local_skill(
        req.params.get("path", ""), bool(req.params.get("force", False))))


@handler(ReqMethod.SKILLS_IMPORT_UPLOAD)
async def skills_import_upload(req, ctx: HandlerContext):
    import base64
    raw_b64 = req.params.get("content_base64", "") or ""
    try:
        data = base64.b64decode(raw_b64) if raw_b64 else b""
    except Exception:
        return AgentResponse(req.request_id, payload={"success": False, "detail": "invalid base64"})
    return AgentResponse(req.request_id, payload=import_upload_skill(
        req.params.get("filename", ""), data, bool(req.params.get("force", False))))


@handler(ReqMethod.SKILLS_MARKETPLACE_LIST)
async def skills_marketplace_list(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload=list_marketplaces())


# ── online marketplaces (clawhub.ai / SkillNet via GitHub search) ──

@handler(ReqMethod.SKILLS_CLAWHUB_SEARCH)
async def skills_clawhub_search(req, ctx: HandlerContext):
    mp = _mp()
    res = await asyncio.to_thread(mp.clawhub_search, req.params.get("q", ""), int(req.params.get("limit", 50)))
    return AgentResponse(req.request_id, payload=res)


@handler(ReqMethod.SKILLS_CLAWHUB_DOWNLOAD)
async def skills_clawhub_download(req, ctx: HandlerContext):
    mp = _mp()
    res = await asyncio.to_thread(mp.clawhub_download, req.params.get("slug", ""), bool(req.params.get("force", False)), req.params.get("meta"))
    return AgentResponse(req.request_id, payload=res)


@handler(ReqMethod.SKILLS_CLAWHUB_GET_TOKEN)
async def skills_clawhub_get_token(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload=_mp().clawhub_get_token())


@handler(ReqMethod.SKILLS_CLAWHUB_SET_TOKEN)
async def skills_clawhub_set_token(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload=_mp().clawhub_set_token(req.params.get("token", "")))


@handler(ReqMethod.SKILLS_SKILLNET_SEARCH)
async def skills_skillnet_search(req, ctx: HandlerContext):
    mp = _mp()
    res = await asyncio.to_thread(mp.skillnet_search, req.params.get("q", ""), int(req.params.get("limit", 20)))
    return AgentResponse(req.request_id, payload=res)


@handler(ReqMethod.SKILLS_SKILLNET_INSTALL)
async def skills_skillnet_install(req, ctx: HandlerContext):
    mp = _mp()
    res = await asyncio.to_thread(mp.skillnet_install, req.params.get("url", ""), bool(req.params.get("force", False)), req.params.get("meta"))
    return AgentResponse(req.request_id, payload=res)


@handler(ReqMethod.SKILLS_SKILLNET_INSTALL_STATUS)
async def skills_skillnet_install_status(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload=_mp().skillnet_install_status(req.params.get("install_id", "")))


@handler(ReqMethod.SKILLS_SKILLNET_EVALUATE)
async def skills_skillnet_evaluate(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload=_mp().skillnet_evaluate(req.params.get("url", "")))


@handler(ReqMethod.SKILLS_TEAMSKILLS_SEARCH)
async def skills_teamskills_search(req, ctx: HandlerContext):
    mp = _mp()
    res = await asyncio.to_thread(mp.teamskills_search, req.params.get("q", ""), int(req.params.get("limit", 50)))
    return AgentResponse(req.request_id, payload=res)


@handler(ReqMethod.SKILLS_TEAMSKILLS_INSTALL)
async def skills_teamskills_install(req, ctx: HandlerContext):
    mp = _mp()
    res = await asyncio.to_thread(mp.teamskills_install, req.params.get("asset_id", ""), bool(req.params.get("force", False)))
    return AgentResponse(req.request_id, payload=res)


@handler(ReqMethod.SKILLS_TEAMSKILLS_INFO)
async def skills_teamskills_info(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload=_mp().teamskills_info())


@handler(ReqMethod.SKILLS_SKILLHUB_SEARCH)
async def skills_skillhub_search(req, ctx: HandlerContext):
    mp = _mp()
    res = await asyncio.to_thread(mp.skillhub_search, req.params.get("q", ""), int(req.params.get("limit", 50)))
    return AgentResponse(req.request_id, payload=res)


@handler(ReqMethod.SKILLS_SKILLHUB_INSTALL)
async def skills_skillhub_install(req, ctx: HandlerContext):
    mp = _mp()
    res = await asyncio.to_thread(mp.skillhub_install, req.params.get("asset_id", ""), bool(req.params.get("force", False)), req.params.get("meta"))
    return AgentResponse(req.request_id, payload=res)


@handler(ReqMethod.SKILLS_SKILLHUB_INFO)
async def skills_skillhub_info(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload=_mp().skillhub_info())
