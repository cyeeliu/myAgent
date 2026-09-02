"""agent_core.blocks — extracted from code.py (s20 comprehensive agent)."""
from types import SimpleNamespace


class _TextBlock(SimpleNamespace):
    type = "text"

    def __init__(self, text: str):
        super().__init__(text=text)

class _ToolUseBlock(SimpleNamespace):
    type = "tool_use"

    def __init__(self, id: str, name: str, input: dict):
        super().__init__(id=id, name=name, input=input)

def _block_attr(block, name, default=None):
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)

def _block_type(block):
    t = _block_attr(block, "type")
    if t:
        return t
    # Infer from shape — blocks hydrated from older DB saves may lack `type`
    # (it was a class attr on _TextBlock/_ToolUseBlock and got dropped by JSON).
    if _block_attr(block, "tool_use_id") is not None:
        return "tool_result"
    if _block_attr(block, "name") is not None and _block_attr(block, "input") is not None:
        return "tool_use"
    if _block_attr(block, "text") is not None:
        return "text"
    return None

def extract_text(content) -> str:
    if not isinstance(content, list):
        return str(content)
    return "\n".join(
        getattr(block, "text", "")
        for block in content
        if getattr(block, "type", None) == "text").strip()

def has_tool_use(content) -> bool:
    # Do not rely on stop_reason alone; the concrete tool_use block is the
    # continuation signal used by the loop.
    return any(getattr(block, "type", None) == "tool_use"
               for block in content)
