"use client";
import { useEffect, useState } from "react";
import type { SessionManager } from "../lib/useSessionManager";
import type { SessionMeta } from "../lib/sessions";

// Same-origin by default (see lib/sessions.ts).
const GATEWAY =
  process.env.NEXT_PUBLIC_GATEWAY_URL ||
  (typeof window !== "undefined" ? window.location.origin : "http://localhost:8000");

type Skill = { name: string; description: string };
type McpServer = { name: string; tools: { name: string; description: string }[] };

// Side panel: session switcher (top) + read-only views of the agent's live
// state (skills/mcp). Fetched from the gateway's read-only endpoints and
// refreshed every few seconds.
export function Sidebar({ sm }: { sm: SessionManager }) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [mcp, setMcp] = useState<McpServer[]>([]);
  const [tab, setTab] = useState<"skills" | "mcp">("skills");

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const sidQ = sm.currentId ? `?sid=${sm.currentId}` : "";
        const [s, m] = await Promise.all([
          fetch(`${GATEWAY}/api/skills`).then((r) => r.json()),
          fetch(`${GATEWAY}/api/mcp${sidQ}`).then((r) => r.json()),
        ]);
        if (cancelled) return;
        setSkills(Array.isArray(s) ? s : []);
        setMcp(Array.isArray(m) ? m : []);
      } catch { /* gateway not up yet */ }
    }
    refresh();
    const id = setInterval(refresh, 4000);
    return () => { cancelled = true; clearInterval(id); };
  }, [sm.currentId]);

  return (
    <aside className="flex w-72 flex-col border-r border-zinc-800 bg-zinc-950 text-sm">
      <SessionList sm={sm} />
      <div className="flex border-b border-zinc-800">
        {(["skills", "mcp"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
                  className={`flex-1 px-2 py-2 uppercase ${tab === t ? "bg-zinc-800 text-cyan-300" : "text-zinc-400"}`}>
            {t}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {tab === "skills" && (skills.length === 0 ? (
          <div className="px-2 py-1 text-xs text-zinc-500">(no skills)</div>
        ) : skills.map((s) => (
          <div key={s.name} className="mb-2 rounded bg-zinc-900 p-2">
            <div className="font-mono text-cyan-400">{s.name}</div>
            <div className="text-xs text-zinc-400">{s.description}</div>
          </div>
        )))}
        {tab === "mcp" && (mcp.length === 0 ? (
          <div className="px-2 py-1 text-xs text-zinc-500">(no MCP servers connected)</div>
        ) : mcp.map((srv) => (
          <div key={srv.name} className="mb-3">
            <div className="mb-1 font-mono text-xs text-cyan-400">{srv.name}</div>
            {srv.tools.length === 0 ? (
              <div className="px-2 text-xs text-zinc-500">(no tools)</div>
            ) : srv.tools.map((t) => (
              <div key={t.name} className="mb-1 rounded bg-zinc-900 px-2 py-1">
                <div className="font-mono text-xs text-zinc-200">{t.name}</div>
                {t.description && (
                  <div className="truncate text-xs text-zinc-500" title={t.description}>{t.description}</div>
                )}
              </div>
            ))}
          </div>
        )))}
      </div>
    </aside>
  );
}

function SessionList({ sm }: { sm: SessionManager }) {
  const { sessions, currentId, switchTo, newSession, removeSession } = sm;
  const sorted = [...sessions].sort((a, b) => b.last_activity - a.last_activity);
  return (
    <div className="flex flex-col border-b border-zinc-800">
      <div className="flex items-center justify-between px-2 py-2">
        <span className="text-xs uppercase tracking-wide text-zinc-500">sessions</span>
        <button
          className="rounded bg-cyan-700 px-2 py-0.5 text-xs hover:bg-cyan-600"
          onClick={() => newSession()}
          title="new session"
        >+ New</button>
      </div>
      <div className="max-h-56 overflow-y-auto px-1 pb-2">
        {sorted.length === 0 && (
          <div className="px-2 py-1 text-xs text-zinc-500">(no sessions)</div>
        )}
        {sorted.map((s: SessionMeta) => {
          const active = s.session_id === currentId;
          return (
            <div key={s.session_id}
                 className={`group mb-1 flex cursor-pointer items-center gap-1 rounded px-2 py-1 ${
                   active ? "bg-zinc-800 text-cyan-300" : "text-zinc-300 hover:bg-zinc-900"
                 }`}
                 onClick={() => switchTo(s.session_id)}>
              <div className="min-w-0 flex-1">
                <div className="truncate">{s.title || "(new session)"}</div>
                <div className="text-xs text-zinc-500">{s.session_id.slice(0, 8)} · {s.history_len} msg</div>
              </div>
              <button
                className="opacity-0 transition group-hover:opacity-100 hover:text-red-400"
                title="delete session"
                onClick={(e) => { e.stopPropagation(); removeSession(s.session_id); }}
              >✕</button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
