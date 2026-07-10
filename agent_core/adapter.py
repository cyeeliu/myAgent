"""agent_core.adapter — extracted from code.py (s20 comprehensive agent)."""
from types import SimpleNamespace
import json
import os
import time as _time
from agent_core.blocks import _TextBlock, _ToolUseBlock, _block_attr, _block_type
from agent_core import model_config

_ADBG = os.environ.get("AGENT_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _adbg(fmt, *args):
    if _ADBG:
        try:
            print("[ADBG] " + (fmt % args if args else fmt), flush=True)
        except Exception:
            print("[ADBG] %s", fmt, flush=True)


def _to_openai_messages(system, messages) -> list[dict]:
    out = []
    if system:
        out.append({"role": "system", "content": system})
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "user":
            if isinstance(content, str):
                out.append({"role": "user", "content": content})
            elif isinstance(content, list):
                # Anthropic bundles tool_result + text in one user message;
                # OpenAI wants each tool result as its own role=tool message.
                tool_msgs, text_parts = [], []
                for b in content:
                    if _block_type(b) == "tool_result":
                        tool_msgs.append({
                            "role": "tool",
                            "tool_call_id": _block_attr(b, "tool_use_id"),
                            "content": str(_block_attr(b, "content", "")),
                        })
                    elif _block_type(b) == "text":
                        text_parts.append(_block_attr(b, "text", ""))
                out.extend(tool_msgs)
                if text_parts:
                    out.append({"role": "user", "content": "\n".join(text_parts)})
        elif role == "assistant":
            if isinstance(content, str):
                out.append({"role": "assistant", "content": content})
            elif isinstance(content, list):
                texts, tool_calls = [], []
                for b in content:
                    if _block_type(b) == "text":
                        t = _block_attr(b, "text", "")
                        if t:
                            texts.append(t)
                    elif _block_type(b) == "tool_use":
                        tool_calls.append({
                            "id": _block_attr(b, "id"),
                            "type": "function",
                            "function": {
                                "name": _block_attr(b, "name"),
                                "arguments": json.dumps(_block_attr(b, "input", {})),
                            },
                        })
                # An assistant turn with neither text nor tool_use (empty content
                # list — a bare stop, or a compaction artifact) is invalid to send
                # to the API: ModelArts.81001 / OpenAI 400 "assistant must have
                # content or tool_calls". Drop it. When tool_calls are present
                # but no text, omit content rather than sending null — some strict
                # backends reject content=null even alongside tool_calls.
                if not texts and not tool_calls:
                    continue
                msg_out = {"role": "assistant"}
                if texts:
                    msg_out["content"] = "\n".join(texts)
                if tool_calls:
                    msg_out["tool_calls"] = tool_calls
                out.append(msg_out)
        elif role == "tool":
            out.append(msg)
    # ── Enforce tool/tool_call consistency ──
    # Compaction (snip_compact/micro_compact/tool_result_budget) can drop an
    # assistant turn but keep its subsequent tool_result, orphaning it. The
    # OpenAI-compatible API rejects a `role=tool` message whose tool_call_id
    # has no preceding assistant tool_call (ModelArts.81001 / OpenAI 400
    # "tool must be a response to a preceding message with tool_calls").
    # Symmetrically, an assistant tool_call with no following tool result is
    # also invalid. Repair both: drop orphaned tool results, then strip any
    # assistant tool_calls that lost their results.
    declared_ids = set()
    for m in out:
        if m.get("role") == "assistant" and "tool_calls" in m:
            for tc in m["tool_calls"]:
                declared_ids.add(tc.get("id"))
    kept = []
    for m in out:
        if m.get("role") == "tool":
            if m.get("tool_call_id") in declared_ids:
                kept.append(m)
        else:
            kept.append(m)
    answered_ids = {m["tool_call_id"] for m in kept if m.get("role") == "tool"}
    final = []
    for m in kept:
        if m.get("role") == "assistant" and "tool_calls" in m:
            live = [tc for tc in m["tool_calls"] if tc.get("id") in answered_ids]
            if live:
                m2 = {"role": "assistant"}
                if "content" in m:
                    m2["content"] = m["content"]
                m2["tool_calls"] = live
                final.append(m2)
            elif "content" in m:
                # tool_calls all orphaned — keep the text part only
                final.append({"role": "assistant", "content": m["content"]})
            # else: drop the assistant entirely
        else:
            final.append(m)
    return final

def _to_openai_tools(tools) -> list[dict] | None:
    if not tools:
        return None
    return [{"type": "function", "function": {
        "name": t["name"],
        "description": t.get("description", ""),
        "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
    }} for t in tools]

def chat_create(model, system=None, messages=None, tools=None,
                max_tokens=8000, stream=False, events=None):
    """Call the OpenAI-compatible chat endpoint and return an Anthropic-shaped
    response ({content: [blocks], stop_reason}) so the rest of the agent stays
    provider-agnostic.

    When stream=True (API path), token deltas are emitted as `token` events and
    tool_call fragments are reassembled; the returned shape is identical. When
    stream=False (CLI path), a single non-streaming call is made — preserving
    the original CLI behavior."""
    oai_msgs = _to_openai_messages(system, messages or [])
    kwargs = {"model": model,
              "messages": oai_msgs,
              "max_tokens": max_tokens}
    oai_tools = _to_openai_tools(tools)
    if oai_tools:
        kwargs["tools"] = oai_tools

    if not stream:
        resp = model_config.client().chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message
        blocks = []
        if msg.content:
            blocks.append(_TextBlock(msg.content))
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            blocks.append(_ToolUseBlock(id=tc.id, name=tc.function.name, input=args))
        stop_map = {"tool_calls": "tool_use", "length": "max_tokens", "stop": "end_turn"}
        return SimpleNamespace(
            content=blocks,
            stop_reason=stop_map.get(choice.finish_reason, choice.finish_reason),
            interrupted=bool(events is not None and getattr(events, "interrupted", False)),
        )

    # Streaming path: emit token deltas, reassemble text + tool_calls.
    # Check `events.interrupted` between chunks so a client Interrupt stops the
    # stream mid-response instead of waiting for the model to finish the turn.
    kwargs["stream"] = True
    text_parts: list[str] = []
    tool_calls: dict[int, dict] = {}
    finish_reason = None
    interrupted = False
    _t0 = _time.monotonic()
    _chunk_no = 0
    if _ADBG:
        _adbg("stream call start model=%r msgs=%d tools=%d", model, len(oai_msgs), len(oai_tools))
    for chunk in model_config.client().chat.completions.create(**kwargs):
        if events is not None and getattr(events, "interrupted", False):
            interrupted = True
            break
        _chunk_no += 1
        if not chunk.choices:
            if _ADBG:
                _adbg("chunk #%d t=%.3f no-choices", _chunk_no, _time.monotonic() - _t0)
            continue
        delta = chunk.choices[0].delta
        # Reasoning models (GLM/DeepSeek/openpangu) carry thinking tokens in a
        # separate field (reasoning_content / reasoning) — NOT in delta.content.
        # Log it so the TTFT gap is explainable; we don't (yet) surface it to the UI.
        if _ADBG and _chunk_no <= 5:
            _rc = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            _adbg("chunk #%d t=%.3f content_len=%d reasoning_len=%d tool_calls=%s",
                  _chunk_no, _time.monotonic() - _t0,
                  len(getattr(delta, "content", None) or ""),
                  len(_rc or ""),
                  bool(getattr(delta, "tool_calls", None)))
        if getattr(delta, "content", None):
            text_parts.append(delta.content)
            if events is not None:
                events.emit("token", {"text": delta.content})
        if getattr(delta, "tool_calls", None):
            for tc in delta.tool_calls:
                idx = tc.index if tc.index is not None else 0
                slot = tool_calls.setdefault(idx, {"id": None, "name": None, "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments
        if chunk.choices[0].finish_reason:
            finish_reason = chunk.choices[0].finish_reason

    if interrupted:
        # Drop partial text/tool_calls — a half-formed tool_use has invalid JSON
        # args and acting on it would be wrong. The streamed tokens already went
        # to the UI; history stays clean for the next turn.
        return SimpleNamespace(content=[], stop_reason="interrupted", interrupted=True)

    blocks = []
    if text_parts:
        blocks.append(_TextBlock("".join(text_parts)))
    for idx in sorted(tool_calls):
        slot = tool_calls[idx]
        try:
            args = json.loads(slot["arguments"] or "{}")
        except json.JSONDecodeError:
            args = {}
        blocks.append(_ToolUseBlock(id=slot["id"], name=slot["name"], input=args))
    stop_map = {"tool_calls": "tool_use", "length": "max_tokens", "stop": "end_turn"}
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_map.get(finish_reason, finish_reason),
        interrupted=False,
    )
