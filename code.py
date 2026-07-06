#!/usr/bin/env python3
"""Backward-compat facade. The agent core lives in the `agent_core/` package.

This file is kept so that `import code` (agent_gateway, tests) and
`python code.py` (interactive CLI) continue to work unchanged. All real
logic was split verbatim into `agent_core/*.py` by `_split.py`; this module
just re-exports the public API.
"""
from agent_core import *  # noqa: F401,F403  (public API via __all__)
# `import *` skips underscore-prefixed names — re-export the ones gateway/tests
# reach for through `code.X`.
from agent_core import (  # noqa: F401
    _TextBlock, _ToolUseBlock, _block_type, _block_attr,
    extract_text, has_tool_use,
    _session_local, _mcp_clients, _load_mcp_config,
    _memory_dir, _memory_index, _parse_frontmatter, _rebuild_index,
    _block_text, _msg_text,
)
from types import SimpleNamespace  # noqa: F401  (gateway/tests use code.SimpleNamespace)

if __name__ == "__main__":
    from agent_core.cli import main
    main()
