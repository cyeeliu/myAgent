"""Config handlers: config.get, config.set, config.save_all, config.validate_model."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from agent_core import agents_flat_to_structured, write_agents_config

from agent_core import model_config

from ...schema.agent import AgentResponse
from ...schema.message import ReqMethod
from ..dispatcher import handler, HandlerContext
from ..helpers import config_get, config_set


# Approved LLM provider base URL hostnames. When non-empty, config writes and
# validate_model reject base_url whose host is not in this set. Leave empty to
# allow any (backward compat for self-hosted / custom endpoints).
_APPROVED_HOSTS: set[str] = set()


def _is_safe_url(url: str) -> tuple[bool, str]:
    """Validate a base_url for SSRF safety. Returns (ok, reason).

    Rejects loopback, private, link-local, and multicast IPs. Also rejects
    the cloud metadata endpoint 169.254.169.254 explicitly."""
    if not url:
        return False, "empty url"
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return False, "no hostname in url"
        # Resolve hostname to IP(s) and check each.
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            # Can't resolve — allow (might resolve later in a different env).
            # But still check if the host is a literal IP.
            try:
                ip = ipaddress.ip_address(host)
                if not _is_safe_ip(ip):
                    return False, f"unsafe IP: {ip}"
            except ValueError:
                pass  # hostname, can't resolve — allow
            return True, ""
        for family, _, _, _, sockaddr in infos:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if not _is_safe_ip(ip):
                return False, f"unsafe resolved IP: {ip} for host {host}"
        return True, ""
    except Exception as e:
        return False, f"parse error: {e}"


def _is_safe_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return False for loopback, private, link-local, multicast, unspecified."""
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast:
        return False
    if ip.is_unspecified:  # 0.0.0.0 / ::
        return False
    return True


def _validate_base_url(base_url: str) -> tuple[bool, str]:
    """Validate base_url against SSRF rules and optional approved-host whitelist."""
    ok, reason = _is_safe_url(base_url)
    if not ok:
        return False, reason
    if _APPROVED_HOSTS:
        host = urlparse(base_url).hostname or ""
        if host not in _APPROVED_HOSTS:
            return False, f"host {host} not in approved list"
    return True, ""


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
        # Validate each model's base_url for SSRF before writing.
        for m in models:
            if isinstance(m, dict):
                bu = m.get("base_url") or ""
                if bu:
                    ok, reason = _validate_base_url(bu)
                    if not ok:
                        return AgentResponse(req.request_id, ok=False,
                                             error=f"unsafe base_url: {reason}")
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
    # SSRF: validate api_base before making an outbound request.
    ok, reason = _validate_base_url(api_base)
    if not ok:
        return AgentResponse(req.request_id, ok=False,
                             error=f"unsafe api_base: {reason}")
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
