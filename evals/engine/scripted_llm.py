"""evals.engine.scripted_llm — mock LLM for zero-cost deterministic evaluation."""
from __future__ import annotations

from typing import Any
from dataclasses import dataclass, field


@dataclass
class ScriptedResponse:
    """A pre-scripted LLM response."""
    content: list[dict]      # content blocks
    interrupted: bool = False
    usage: dict = field(default_factory=dict)


def make_text_response(text: str) -> ScriptedResponse:
    return ScriptedResponse(content=[{"type": "text", "text": text}])


def make_tool_use_response(tool_id: str, tool_name: str, tool_input: dict) -> ScriptedResponse:
    return ScriptedResponse(content=[{
        "type": "tool_use", "id": tool_id, "name": tool_name, "input": tool_input,
    }])


def make_mixed_response(text: str, tool_id: str, tool_name: str, tool_input: dict) -> ScriptedResponse:
    return ScriptedResponse(content=[
        {"type": "text", "text": text},
        {"type": "tool_use", "id": tool_id, "name": tool_name, "input": tool_input},
    ])


class ScriptedLLM:
    """Replace adapter.chat_create with a script of predetermined responses.

    Usage:
        script = [make_tool_use_response("t1", "grep", {"pattern": "foo"}),
                  make_text_response("found 3 matches")]
        mock = ScriptedLLM(script)
        adapter.chat_create = mock  # monkeypatch
    """

    def __init__(self, script: list[ScriptedResponse]):
        self._script = list(script)
        self._index = 0
        self.calls = []  # record all calls for debugging

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self._index >= len(self._script):
            # Return empty text response if script exhausted
            resp = make_text_response("")
        else:
            resp = self._script[self._index]
            self._index += 1

        # Return a SimpleNamespace to mimic the adapter response
        from types import SimpleNamespace
        return SimpleNamespace(
            content=resp.content,
            interrupted=resp.interrupted,
            usage=resp.usage,
        )

    @property
    def exhausted(self) -> bool:
        return self._index >= len(self._script)
