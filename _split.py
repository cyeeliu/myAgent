#!/usr/bin/env python3
"""One-shot splitter: carve code.py into agent_core/*.py verbatim, then auto-resolve
cross-module imports by scanning referenced names against the global def table.

SAFETY GUARD: This script reads code.py and overwrites all agent_core/*.py modules.
If code.py is now a thin re-export facade (which it is — the real logic lives in
agent_core/), running this script would DESTROY all hand-edited modules. The guard
below detects the facade and refuses to run. Use --force to override (after restoring
code.py from git with the full single-file source).
"""
import sys
# ── Facade detection guard ──
_src_check = Path('code.py').read_text() if Path('code.py').exists() else ""
# A facade is typically < 200 lines and consists mostly of re-exports.
if len(_src_check.splitlines()) < 200 and "--force" not in sys.argv:
    print("REFUSING TO RUN: code.py appears to be a thin re-export facade",
          f"({len(_src_check.splitlines())} lines).", file=sys.stderr)
    print("Running _split.py now would OVERWRITE all hand-edited agent_core/*.py",
          "modules with re-generated content, destroying your changes.",
          file=sys.stderr)
    print("To proceed: restore code.py from git with the full single-file source,",
          "then re-run. Or use --force to override (DANGEROUS).", file=sys.stderr)
    sys.exit(1)

import ast, re, json, os
from pathlib import Path

SRC = Path('code.py').read_text()
SRCLINES = SRC.splitlines()
tree = ast.parse(SRC)

# Collect top-level nodes: (start, end, kind, names[])
nodes = []
for node in tree.body:
    s, e = node.lineno, node.end_lineno
    kind = type(node).__name__
    names = []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names = [node.name]
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name): names.append(t.id)
            elif isinstance(t, ast.Attribute): names.append(t.attr)
    elif isinstance(node, ast.AnnAssign):
        if isinstance(node.target, ast.Name): names = [node.target.id]
    elif isinstance(node, ast.Import):
        names = [a.asname or a.name.split('.')[0] for a in node.names]
    elif isinstance(node, ast.ImportFrom):
        names = [a.asname or a.name for a in node.names]
    nodes.append((s, e, kind, names))

# Map every top-level name -> (module, start, end). Built from explicit assignment below.
# Module assignment by name. We list each name -> module.
M = {}  # name -> module

def assign(mod, *names):
    for n in names: M[n] = mod

# env.py — foundational globals, paths, constants, terminal_print
assign('env',
    'REPO_ROOT','_wd_local','workdir','set_workdir','client','MODEL','PRIMARY_MODEL','FALLBACK_MODEL',
    '_transcript_dir','_tool_results_dir',
    'DEFAULT_MAX_TOKENS','ESCALATED_MAX_TOKENS','MAX_RETRIES','MAX_CONSECUTIVE_529','MAX_RECOVERY_RETRIES',
    'BASE_DELAY_MS','CONTEXT_LIMIT','KEEP_RECENT_TOOL_RESULTS','PERSIST_THRESHOLD','CONTINUATION_PROMPT',
    'PROMPT','CLI_ACTIVE','terminal_print')
# blocks.py
assign('blocks','_TextBlock','_ToolUseBlock','_block_attr','_block_type','extract_text','has_tool_use')
# adapter.py
assign('adapter','_to_openai_messages','_to_openai_tools','chat_create')
# session.py
assign('session','EVENT_KINDS','EventSink','TerminalSink','ChannelSink','RecordingSink',
       'Permission','CliPermission','FuturePermission','Session')
# skills.py
assign('skills','SKILLS_DIR','SKILL_REGISTRY','_parse_frontmatter','scan_skills','list_skills','load_skill')
# tasks.py
assign('tasks','_tasks_dir','CURRENT_TODOS','Task','_task_path','create_task','save_task','load_task',
       'list_tasks','get_task_json','can_start','claim_task','complete_task')
# worktrees.py
assign('worktrees','_worktrees_dir','VALID_WT_NAME','validate_worktree_name','run_git','log_event',
       'create_worktree','bind_task_to_worktree','_count_worktree_changes','remove_worktree','keep_worktree')
# bus.py
assign('bus','_mailbox_dir','MessageBus','BUS','ProtocolState','pending_requests',
       'new_request_id','match_response','consume_lead_inbox')
# teammates.py  (active_teammates lives here)
assign('teammates','active_teammates','IDLE_POLL_INTERVAL','IDLE_TIMEOUT','scan_unclaimed_tasks',
       'idle_poll','spawn_teammate_thread','_teammate_submit_plan','run_request_shutdown',
       'run_request_plan','run_review_plan')
# hooks.py
assign('hooks','HOOKS','register_hook','trigger_hooks','DENY_LIST','DESTRUCTIVE','check_permission',
       'permission_hook','log_hook','large_output_hook','user_prompt_hook','stop_hook')
# subagent.py
assign('subagent','SUB_SYSTEM','SUB_TOOLS','SUB_HANDLERS','spawn_subagent')
# compaction.py
assign('compaction','estimate_size','block_type','message_has_tool_use','is_tool_result_message',
       'collect_tool_results','persist_large_output','tool_result_budget','snip_compact','micro_compact',
       'write_transcript','summarize_history','compact_history','reactive_compact')
# recovery.py
assign('recovery','RecoveryState','retry_delay','with_retry','is_prompt_too_long_error')
# background.py
assign('background','_bg_counter','background_tasks','background_results','background_lock',
       'is_slow_operation','should_run_background','start_background_task','collect_background_results')
# cron.py
assign('cron','_durable_path','CronJob','scheduled_jobs','cron_queue','cron_lock','_last_fired',
       '_cron_field_matches','cron_matches','_validate_cron_field','validate_cron',
       'save_durable_jobs','load_durable_jobs','schedule_job','cancel_job','cron_scheduler_loop',
       'consume_cron_queue','run_schedule_cron','run_list_crons','run_cancel_cron')
# mcp.py
assign('mcp','MCPClient','mcp_clients','_session_local','set_current_session','get_current_session',
       '_mcp_clients','_load_mcp_config','_DISALLOWED_CHARS','normalize_mcp_name',
       '_mock_server_docs','_mock_server_deploy','MOCK_SERVERS','connect_mcp','assemble_tool_pool')
# memory.py
assign('memory','MEMORY_TYPES','CONSOLIDATE_THRESHOLD','_memory_dir','_memory_index',
       'write_memory_file','_rebuild_index','read_memory_index','read_memory_file','list_memory_files',
       '_block_text','_msg_text','_memory_llm','select_relevant_memories','load_memories',
       'extract_memories','consolidate_memories')
# prompt.py
assign('prompt','PROMPT_SECTIONS','assemble_system_prompt')
# tools.py
assign('tools','safe_path','run_bash','run_read','run_write','run_edit','run_glob','call_tool_handler',
       '_normalize_todos','run_todo_write',
       'run_create_worktree','run_remove_worktree','run_keep_worktree',
       'run_create_task','run_list_tasks','run_get_task','run_claim_task','run_complete_task',
       'run_spawn_teammate','run_send_message','run_check_inbox','run_connect_mcp',
       'BUILTIN_TOOLS','BUILTIN_HANDLERS')
# context.py
assign('context','update_context','prepare_context','build_user_content','inject_background_notifications')
# loop.py
assign('loop','call_llm','agent_loop','print_turn_assistants','cron_autorun_loop')

# Note: memory._parse_frontmatter (the manual one at 2509) is a SEPARATE def from skills._parse_frontmatter.
# Both exist in code.py; the second shadows the first. We keep skills' yaml version in skills.py and
# memory's manual version in memory.py. They agree on flat frontmatter. Handle the 2509 node specially.

# Build module -> list of (start,end) ranges, in source order.
# We need to map each node to a module. A node maps by its primary name. For the duplicate
# _parse_frontmatter, the 717 node -> skills, the 2509 node -> memory.
module_ranges = {m: [] for m in set(M.values())}
# Also env gets the top import block (lines 15-30) and readline try (22-27) and the dot-dir For (55-57).
# We'll handle those by line-range explicitly.

# Map each top-level node to a module by name lookup.
for (s, e, kind, names) in nodes:
    if not names:
        continue
    primary = names[0]
    # Special-case the two _parse_frontmatter by line number.
    if primary == '_parse_frontmatter':
        mod = 'skills' if s < 1000 else 'memory'
    elif primary in M:
        mod = M[primary]
    else:
        # Unassigned top-level name — collect for reporting.
        mod = None
    if mod:
        module_ranges[mod].append((s, e, primary))

# Explicit env additions: the import block (15-20), readline try (22-27), OpenAI/dotenv (29-30),
# the dot-dir For (55-57). These are top-level nodes already in `nodes`; they'll be assigned below
# by name (ast,json,... -> env; OpenAI,load_dotenv -> env). The For has no name — handle by line.
for (s, e, kind, names) in nodes:
    if kind == 'For' and 50 <= s <= 60:
        module_ranges['env'].append((s, e, '<dotdir-for>'))
    if kind == 'Try' and 20 <= s <= 30:
        module_ranges['env'].append((s, e, '<readline>'))

# Sort each module's ranges by start line.
for m in module_ranges:
    module_ranges[m].sort(key=lambda r: r[0])

# Emit each module's verbatim source.
Path('agent_core').mkdir(exist_ok=True)
module_src = {}
for m, ranges in module_ranges.items():
    parts = []
    for (s, e, name) in ranges:
        parts.append('\n'.join(SRCLINES[s-1:e]))
    module_src[m] = '\n\n'.join(parts) + '\n'

# ---- Auto import resolution ----
# Build global name -> module (for cross-module refs). Skip names defined in >1 module.
name_defs = {}  # name -> set of modules
for (s, e, kind, names) in nodes:
    for n in names:
        if n: name_defs.setdefault(n, set()).add(M.get(n, '?'))
# For _parse_frontmatter: defined in both skills and memory. Don't auto-import it.
ambiguous = {n for n, ms in name_defs.items() if len(ms) > 1 or '?' in ms}

# stdlib name -> import statement
STDLIB = {
    'json':'import json','os':'import os','time':'import time','threading':'import threading',
    're':'import re','subprocess':'import subprocess','random':'import random','queue':'import queue',
    'ast':'import ast',
    'Path':'from pathlib import Path','datetime':'from datetime import datetime',
    'dataclass':'from dataclasses import dataclass, asdict, field',
    'asdict':'from dataclasses import dataclass, asdict, field',
    'field':'from dataclasses import dataclass, asdict, field',
    'SimpleNamespace':'from types import SimpleNamespace',
    'yaml':'import yaml','OpenAI':'from openai import OpenAI','load_dotenv':'from dotenv import load_dotenv',
}
# names that are builtin/always-available (don't import)
BUILTINS = set(__builtins__.__dict__.keys()) | {'__builtins__'}

def referenced_names(src):
    try: t = ast.parse(src)
    except SyntaxError: return set()
    refs = set()
    for n in ast.walk(t):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            refs.add(n.id)
    return refs

# For each module, compute cross imports + stdlib imports.
for m in module_src:
    src = module_src[m]
    refs = referenced_names(src)
    # names this module defines locally
    local_defs = set()
    try:
        for n in ast.parse(src).body:
            if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)): local_defs.add(n.name)
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name): local_defs.add(t.id)
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                local_defs.add(n.target.id)
    except SyntaxError: pass
    cross = {}  # module -> set(names)
    stdlib_needed = set()
    for r in refs:
        if r in local_defs or r in BUILTINS: continue
        if r in M and M[r] != m:
            cross.setdefault(M[r], set()).add(r)
        elif r in STDLIB:
            stdlib_needed.add(r)
    # build header
    header = [f'"""agent_core.{m} — extracted from code.py (s20 comprehensive agent)."""']
    # stdlib imports (dedupe by statement)
    seen_stmt = set()
    for name in sorted(stdlib_needed):
        stmt = STDLIB[name]
        if stmt not in seen_stmt:
            header.append(stmt); seen_stmt.add(stmt)
    # cross-module imports
    for mod in sorted(cross):
        names = sorted(cross[mod])
        header.append(f'from agent_core.{mod} import ' + ', '.join(names))
    module_src[m] = '\n'.join(header) + '\n\n\n' + src

for m, src in module_src.items():
    Path(f'agent_core/{m}.py').write_text(src)

print('Wrote modules:', sorted(module_src))
# Report any unassigned top-level names
assigned = set(M)
all_top = set()
for (s,e,k,names) in nodes:
    for n in names: all_top.add(n)
unassigned = all_top - assigned
print('Unassigned top-level names:', sorted(unassigned))
print('Ambiguous (defined in >1 module):', sorted(ambiguous))
