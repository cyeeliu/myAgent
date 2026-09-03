"""agent_core.structured — structured output helpers.

Provides utilities for requesting and validating structured (JSON) output
from the LLM.  Two modes:

1. **JSON mode** — `request_json(prompt, schema)` asks the model to produce
   JSON conforming to a schema, using `response_format={"type": "json_object"}`.
   The response is parsed and validated.

2. **Tool-forced** — `request_via_tool(prompt, tool_name, tool_schema)` uses
   `tool_choice` to force the model to call a specific tool, guaranteeing
   structured output via the tool's input schema.

Both go through `adapter.chat_create` so monkeypatch propagates.
"""
from __future__ import annotations

import json
from typing import Any
from types import SimpleNamespace

from agent_core import adapter


def request_json(
    prompt: str,
    schema: dict | None = None,
    model: str = "",
    system: str = "",
    max_tokens: int = 4000,
    timeout: int | None = 30,
) -> dict | None:
    """Request structured JSON output from the LLM.

    Args:
        prompt: The user prompt asking for structured data.
        schema: Optional JSON schema describing the expected shape.
            Included in the system prompt as guidance; the model is also
            told to respond with JSON only.
        model: Model ID (empty = use default).
        system: Additional system prompt text.
        max_tokens: Max response tokens.
        timeout: Call timeout in seconds.

    Returns:
        Parsed dict on success, None on failure.
    """
    from agent_core import model_config

    sys_parts = [system, "You must respond with valid JSON only. No markdown, no prose."]
    if schema:
        sys_parts.append(f"The JSON must conform to this schema:\n```json\n{json.dumps(schema, indent=2)}\n```")
    sys_text = "\n\n".join(p for p in sys_parts if p)

    messages = [{"role": "user", "content": prompt}]
    mdl = model or model_config.model()

    try:
        resp = adapter.chat_create(
            model=mdl,
            system=sys_text,
            messages=messages,
            tools=None,
            max_tokens=max_tokens,
            stream=False,
            timeout=timeout,
            response_format={"type": "json_object"},
        )
        # Extract text from response
        for block in resp.content:
            if hasattr(block, "text"):
                text = block.text
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    # Try to extract JSON from markdown code blocks
                    return _extract_json(text)
        return None
    except Exception:
        return None


def request_via_tool(
    prompt: str,
    tool_name: str,
    tool_schema: dict,
    model: str = "",
    system: str = "",
    max_tokens: int = 4000,
    timeout: int | None = 30,
) -> dict | None:
    """Force the model to produce structured output via a specific tool call.

    Uses `tool_choice` to force the model to call the named tool, guaranteeing
    the output matches the tool's input schema.

    Args:
        prompt: The user prompt.
        tool_name: The tool the model must call.
        tool_schema: The tool's input schema (JSON Schema).
        model: Model ID (empty = default).
        system: Additional system prompt.
        max_tokens: Max response tokens.
        timeout: Call timeout.

    Returns:
        The tool call's input arguments as a dict, or None on failure.
    """
    from agent_core import model_config

    sys_text = system or f"You must use the {tool_name} tool to respond."
    messages = [{"role": "user", "content": prompt}]
    mdl = model or model_config.model()

    tools = [{
        "type": "function",
        "function": {
            "name": tool_name,
            "description": f"Structured output via {tool_name}",
            "parameters": tool_schema,
        },
    }]

    tool_choice = {"type": "function", "function": {"name": tool_name}}

    try:
        resp = adapter.chat_create(
            model=mdl,
            system=sys_text,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            stream=False,
            timeout=timeout,
            tool_choice=tool_choice,
        )
        # Extract tool_use block
        for block in resp.content:
            if hasattr(block, "name") and block.name == tool_name:
                return block.input if isinstance(block.input, dict) else None
        return None
    except Exception:
        return None


def _extract_json(text: str) -> dict | None:
    """Try to extract JSON from text that might have markdown fences or
    surrounding prose."""
    # Try markdown code block
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        try:
            return json.loads(text[start:end].strip())
        except (json.JSONDecodeError, ValueError):
            pass
    if "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        try:
            return json.loads(text[start:end].strip())
        except (json.JSONDecodeError, ValueError):
            pass
    # Try finding first { and last }
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        try:
            return json.loads(text[first:last + 1])
        except json.JSONDecodeError:
            pass
    return None


def validate_against_schema(data: dict, schema: dict) -> list[str]:
    """Lightweight JSON Schema validation. Returns a list of error messages
    (empty = valid).  Supports type, required, properties, items, enum."""
    errors = []

    def _check_type(value, expected, path):
        type_map = {
            "string": str, "number": (int, float),
            "integer": int, "boolean": bool,
            "object": dict, "array": list,
        }
        py_type = type_map.get(expected)
        if py_type and not isinstance(value, py_type):
            errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
        if expected == "integer" and isinstance(value, float) and not value.is_integer():
            errors.append(f"{path}: expected integer, got float")

    def _validate(obj, sch, path="root"):
        if "type" in sch:
            _check_type(obj, sch["type"], path)
        if "enum" in sch:
            if obj not in sch["enum"]:
                errors.append(f"{path}: value {obj} not in enum {sch['enum']}")
        if sch.get("type") == "object":
            for req in sch.get("required", []):
                if req not in obj:
                    errors.append(f"{path}: missing required field '{req}'")
            for key, sub_schema in sch.get("properties", {}).items():
                if key in obj:
                    _validate(obj[key], sub_schema, f"{path}.{key}")
        if sch.get("type") == "array" and "items" in sch:
            for i, item in enumerate(obj):
                _validate(item, sch["items"], f"{path}[{i}]")

    _validate(data, schema)
    return errors
