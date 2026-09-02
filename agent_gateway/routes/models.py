"""Global model configuration routes (``.agents/model.json``)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent_core import model_config
from agent_gateway.schemas import ModelConfig

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
async def get_models():
    """Current model config with api_key masked. The raw key never leaves this."""
    return model_config.get_config_masked()


@router.put("")
async def update_models(body: ModelConfig):
    """Persist model config. Empty api_key preserves the existing on-disk key.
    Takes effect next turn (loop re-reads model_config.model() each round).

    Validates base_url for SSRF safety before writing."""
    if body.base_url:
        from agent_gateway.common.e2a.handlers.config import _validate_base_url
        ok, reason = _validate_base_url(body.base_url)
        if not ok:
            raise HTTPException(status_code=400, detail=f"unsafe base_url: {reason}")
    model_config.write_config(body.model_id, body.base_url,
                              body.api_key, body.fallback_model)
    return {"ok": True}
