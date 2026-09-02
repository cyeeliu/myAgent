"use client";
import { useEffect, useState } from "react";
import { listAgents, deleteAgent, type AgentDef } from "../lib/agents";

// Sidebar list of defined agents. Polls /api/agents every 4s (mirrors the
// skills/mcp refresh cadence). + 新建 → onSelect(null) (empty editor form);
// clicking a row → onSelect(name).
export function AgentList({
  selected,
  onSelect,
}: {
  selected: string | null;
  onSelect: (n: string | null) => void;
}) {
  const [agents, setAgents] = useState<AgentDef[]>([]);

  const refresh = async () => setAgents(await listAgents());

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      const a = await listAgents();
      if (!cancelled) setAgents(a);
    }
    poll();
    const id = setInterval(poll, 4000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  return (
    <div className="flex flex-col border-b border-paper-300/70">
      <div className="flex items-center justify-between px-3 py-2">
        <span className="px-1 text-xs uppercase tracking-wide text-paper-500">智能体</span>
        <button
          className="rounded-md border border-paper-300 px-2 py-1 text-xs font-medium text-paper-700 transition hover:bg-paper-200"
          onClick={() => onSelect(null)}
          title="new agent"
        >+ 新建</button>
      </div>
      <div className="max-h-60 overflow-y-auto px-2 pb-2.5">
        {agents.length === 0 && (
          <div className="px-2 py-1 text-xs text-paper-500">(no agents)</div>
        )}
        {agents.map((a) => {
          const active = a.name === selected;
          return (
            <div
              key={a.name}
              className={`group mb-1 flex cursor-pointer items-center gap-1.5 rounded-lg px-2.5 py-2 transition ${
                active ? "bg-clay-100 text-paper-900" : "text-paper-700 hover:bg-paper-200"
              }`}
              onClick={() => onSelect(a.name)}
            >
              <div className="min-w-0 flex-1">
                <div className="truncate font-mono text-[13px]">{a.name}</div>
                <div className="truncate text-[10px] text-paper-500">
                  {a.description || "(no description)"}
                </div>
              </div>
              <button
                className="opacity-0 transition group-hover:opacity-100 hover:text-red-500"
                title="delete agent"
                onClick={async (e) => {
                  e.stopPropagation();
                  if (!confirm(`Delete agent "${a.name}"?`)) return;
                  await deleteAgent(a.name).catch(() => {});
                  if (selected === a.name) onSelect(null);
                  refresh();
                }}
              >✕</button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
