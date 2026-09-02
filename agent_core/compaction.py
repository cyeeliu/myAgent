"""agent_core.compaction — extracted from code.py (s20 comprehensive agent)."""
from pathlib import Path
import copy
import json
import time
from agent_core import adapter
from agent_core.blocks import extract_text
from agent_core.env import KEEP_RECENT_TOOL_RESULTS, MODEL, PERSIST_THRESHOLD, _tool_results_dir, _transcript_dir


def estimate_size(messages: list) -> int:
    return len(json.dumps(messages, default=str))


def estimate_tokens(messages: list, system: str = "", tools: list | None = None) -> int:
    """Rough token count of the full context the LLM sees: system prompt +
    messages + tool schemas. estimate_size is char-based (json.dumps length);
    we convert with the ~4 chars/token heuristic. Mixed CJK/English content
    makes this approximate, but it gives the UI a sensible, stable unit instead
    of raw JSON chars (which jumped alarmingly on large tool results). The
    auto-compact trigger (context.py) compares estimate_size//4 against
    AUTO_COMPACT_WINDOW (tokens), and the ToolPanel stat denominator is the
    same AUTO_COMPACT_WINDOW — one env var governs both."""
    chars = estimate_size(messages) + len(system or "")
    if tools:
        chars += estimate_size(tools)
    return chars // 4

def block_type(block):
    return block.get("type") if isinstance(block, dict) else getattr(block, "type", None)

def message_has_tool_use(message: dict) -> bool:
    if message.get("role") != "assistant":
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(block_type(block) == "tool_use" for block in content)

def is_tool_result_message(message: dict) -> bool:
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(block, dict) and block.get("type") == "tool_result"
               for block in content)

def collect_tool_results(messages: list):
    found = []
    for mi, msg in enumerate(messages):
        content = msg.get("content")
        if msg.get("role") != "user" or not isinstance(content, list):
            continue
        for bi, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                found.append((mi, bi, block))
    return found

def persist_large_output(tool_use_id: str, output: str) -> str:
    if len(output) <= PERSIST_THRESHOLD:
        return output
    _tool_results_dir().mkdir(parents=True, exist_ok=True)
    path = _tool_results_dir() / f"{tool_use_id}.txt"
    if not path.exists():
        path.write_text(output)
    return (f"<persisted-output>\nFull output: {path}\n"
            f"Preview:\n{output[:2000]}\n</persisted-output>")

def tool_result_budget(messages: list, max_bytes: int = 200_000) -> list:
    if not messages:
        return messages
    last = messages[-1]
    content = last.get("content")
    if last.get("role") != "user" or not isinstance(content, list):
        return messages
    blocks = [(i, b) for i, b in enumerate(content)
              if isinstance(b, dict) and b.get("type") == "tool_result"]
    total = sum(len(str(b.get("content", ""))) for _, b in blocks)
    if total <= max_bytes:
        return messages
    # Build a NEW content list with NEW block dicts — never mutate the shared
    # block in-place (it's the same object in session.record via append_both).
    new_content = [copy.deepcopy(b) if isinstance(b, dict) else b for b in content]
    new_blocks = [(i, b) for i, b in enumerate(new_content)
                  if isinstance(b, dict) and b.get("type") == "tool_result"]
    total = sum(len(str(b.get("content", ""))) for _, b in new_blocks)
    for _, block in sorted(new_blocks,
                           key=lambda pair: len(str(pair[1].get("content", ""))),
                           reverse=True):
        if total <= max_bytes:
            break
        text = str(block.get("content", ""))
        block["content"] = persist_large_output(
            block.get("tool_use_id", "unknown"), text)
        total = sum(len(str(b.get("content", ""))) for _, b in new_blocks)
    # Return a new message list with the last message's content replaced.
    new_last = {**last, "content": new_content}
    return messages[:-1] + [new_last]

def snip_compact(messages: list, max_messages: int = 50) -> list:
    if len(messages) <= max_messages:
        return messages
    head_end, tail_start = 3, len(messages) - (max_messages - 3)
    if head_end > 0 and message_has_tool_use(messages[head_end - 1]):
        while head_end < len(messages) and is_tool_result_message(messages[head_end]):
            head_end += 1
    if (tail_start > 0 and tail_start < len(messages)
            and is_tool_result_message(messages[tail_start])
            and message_has_tool_use(messages[tail_start - 1])):
        tail_start -= 1
    if head_end >= tail_start:
        return messages
    snipped = tail_start - head_end
    return (messages[:head_end]
            + [{"role": "user", "content": f"[snipped {snipped} messages]"}]
            + messages[tail_start:])

def micro_compact(messages: list) -> list:
    tool_results = collect_tool_results(messages)
    if len(tool_results) <= KEEP_RECENT_TOOL_RESULTS:
        return messages
    # Build NEW message dicts + NEW content lists + NEW block dicts — never
    # mutate the shared block in-place (it's the same object in session.record).
    compact_indices = set()
    for mi, bi, block in tool_results[:-KEEP_RECENT_TOOL_RESULTS]:
        if len(str(block.get("content", ""))) > 120:
            compact_indices.add((mi, bi))
    if not compact_indices:
        return messages
    new_messages = []
    for mi, msg in enumerate(messages):
        if mi not in {m for m, _ in compact_indices}:
            new_messages.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            new_messages.append(msg)
            continue
        new_content = []
        for bi, block in enumerate(content):
            if (mi, bi) in compact_indices and isinstance(block, dict):
                new_block = {**block, "content": "[Earlier tool result compacted. Re-run if needed.]"}
                new_content.append(new_block)
            else:
                new_content.append(block)
        new_messages.append({**msg, "content": new_content})
    return new_messages

def write_transcript(messages: list) -> Path:
    _transcript_dir().mkdir(parents=True, exist_ok=True)
    path = _transcript_dir() / f"transcript_{int(time.time())}.jsonl"
    # encoding="utf-8" + ensure_ascii=False so CJK/emoji are written as real
    # characters, not \uXXXX escapes — the transcript is for human/UTF-8 tool
    # inspection and the default ascii-escaping mangled Chinese into noise.
    with path.open("w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str, ensure_ascii=False) + "\n")
    return path

def summarize_history(messages: list) -> str:
    conversation = json.dumps(messages, default=str, ensure_ascii=False)[:80000]
    prompt = ("Summarize this coding-agent conversation so work can continue. "
              "Preserve current goal, key findings, changed files, remaining work, "
              "and user constraints.\n\n" + conversation)
    response = adapter.chat_create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000)
    return extract_text(response.content) or "(empty summary)"

def compact_history(messages: list) -> list:
    transcript = write_transcript(messages)
    print(f"  \033[36m[compact] transcript saved: {transcript}\033[0m")
    summary = summarize_history(messages)
    return [{"role": "user", "content": f"[Compacted]\n\n{summary}"}]

def reactive_compact(messages: list) -> list:
    transcript = write_transcript(messages)
    print(f"  \033[31m[reactive compact] transcript saved: {transcript}\033[0m")
    tail_start = max(0, len(messages) - 5)
    if (tail_start > 0 and tail_start < len(messages)
            and is_tool_result_message(messages[tail_start])
            and message_has_tool_use(messages[tail_start - 1])):
        tail_start -= 1
    try:
        summary = summarize_history(messages[:tail_start])
    except Exception:
        summary = "Earlier conversation was trimmed after a prompt-too-long error."
    return [{"role": "user", "content": f"[Reactive compact]\n\n{summary}"},
            *messages[tail_start:]]
