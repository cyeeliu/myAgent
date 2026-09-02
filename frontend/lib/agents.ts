// Gateway agent-definition API: list / create / update / delete. Mirrors
// agent_gateway.main /api/agents routes. Reuses the GATEWAY origin from
// sessions.ts (same-origin behind nginx, fallback localhost:8000).
import { GATEWAY } from "./sessions";

export type AgentDef = {
  name: string;
  description: string;
  prompt: string;
  model: string | null;
  tools: string[];
};

// Tool names offered in the AgentEditor checkboxes. Subset of BUILTIN_TOOLS
// that makes sense for a subagent (the full builtin set is available to the
// main agent; subagents rarely need teammate/cron/worktree tools).
export const AGENT_TOOL_OPTIONS = [
  "bash", "read_file", "write_file", "edit_file", "glob",
  "todo_write", "task", "load_skill",
];

export async function listAgents(): Promise<AgentDef[]> {
  try {
    const r = await fetch(`${GATEWAY}/api/agents`);
    if (!r.ok) return [];
    const a = await r.json();
    return Array.isArray(a) ? a : [];
  } catch {
    return [];
  }
}

export async function getAgent(name: string): Promise<AgentDef | null> {
  try {
    const all = await listAgents();
    return all.find((a) => a.name === name) ?? null;
  } catch {
    return null;
  }
}

export async function createAgent(a: AgentDef): Promise<AgentDef> {
  const r = await fetch(`${GATEWAY}/api/agents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(a),
  });
  if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
  return r.json();
}

export async function updateAgent(name: string, a: Omit<AgentDef, "name">): Promise<AgentDef> {
  const r = await fetch(`${GATEWAY}/api/agents/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(a),
  });
  if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
  return r.json();
}

export async function deleteAgent(name: string): Promise<void> {
  const r = await fetch(`${GATEWAY}/api/agents/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!r.ok && r.status !== 404) throw new Error(`HTTP ${r.status}`);
}
