"""Global model configuration routes (``.agents/model.json``)."""
from __future__ import annotations

from fastapi import APIRouter

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
    Takes effect next turn (loop re-reads model_config.model() each round)."""
    model_config.write_config(body.model_id, body.base_url,
                              body.api_key, body.fallback_model)
    return {"ok": True}
