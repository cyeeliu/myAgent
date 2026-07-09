"use client";
import { useEffect, useState } from "react";
import type { SessionManager } from "../lib/useSessionManager";
import type { SessionMeta } from "../lib/sessions";
import type { View } from "../app/page";
import { AgentList } from "./AgentList";

const GATEWAY =
  process.env.NEXT_PUBLIC_GATEWAY_URL ||
  (typeof window !== "undefined" ? window.location.origin : "http://localhost:8000");

type Skill = { name: string; description: string };
type McpServer = { name: string; tools: { name: string; description: string }[] };

const NAV_ITEMS: { key: View; label: string }[] = [
  { key: "sessions", label: "会话" },
  { key: "agents", label: "智能体" },
  { key: "model", label: "模型" },
];

function Spark({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 12 12" fill="currentColor" aria-hidden>
      <path d="M6 0l1.6 3.4L11 5l-3.4 1.6L6 10 4.4 6.6 1 5l3.4-1.6z" />
    </svg>
  );
}

export function Sidebar({
  sm,
  view,
  setView,
  selectedAgent,
  setSelectedAgent,
}: {
  sm: SessionManager;
  view: View;
  setView: (v: View) => void;
  selectedAgent: string | null;
  setSelectedAgent: (n: string | null) => void;
}) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [mcp, setMcp] = useState<McpServer[]>([]);
  const [tab, setTab] = useState<"skills" | "mcp">("skills");
  const [toolsOpen, setToolsOpen] = useState(false);

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
    <aside className="flex w-72 flex-col border-r border-paper-300/70 bg-paper-150 text-sm">
      <div className="flex items-center gap-2 px-4 py-3.5 text-paper-800">
        <span className="flex h-6 w-6 items-center justify-center rounded-md bg-clay-500 text-white">
          <Spark size={12} />
        </span>
        <span className="font-semibold">myAgent</span>
      </div>

      {/* 3-column nav */}
      <nav className="flex gap-1 px-2 py-1.5">
        {NAV_ITEMS.map((it) => {
          const active = view === it.key;
          return (
            <button
              key={it.key}
              onClick={() => setView(it.key)}
              className={`flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition ${
                active
                  ? "bg-clay-100 text-clay-600"
                  : "text-paper-500 hover:bg-paper-200 hover:text-paper-700"
              }`}
            >
              {it.label}
            </button>
          );
        })}
      </nav>

      {/* active view's context list */}
      <div className="flex-1 overflow-y-auto">
        {view === "sessions" && <SessionList sm={sm} />}
        {view === "agents" && (
          <AgentList selected={selectedAgent} onSelect={setSelectedAgent} />
        )}
        {view === "model" && null}
      </div>

      {/* collapsible skills/mcp bottom region */}
      <div className="border-t border-paper-300/70">
        <button
          onClick={() => setToolsOpen((o) => !o)}
          className="flex w-full items-center justify-between px-3 py-2 text-xs uppercase tracking-wide text-paper-500 hover:text-paper-700"
        >
          <span>skills / mcp</span>
          <span className={`transition-transform ${toolsOpen ? "rotate-90" : ""}`}>▸</span>
        </button>
        {toolsOpen && (
          <>
            <div className="flex border-b border-paper-300/70">
              {(["skills", "mcp"] as const).map((t) => (
                <button key={t} onClick={() => setTab(t)}
                        className={`flex-1 px-2 py-2 text-xs font-medium uppercase tracking-wide transition ${
                          tab === t ? "text-clay-600" : "text-paper-500 hover:text-paper-700"
                        }`}>
                  {t}
                </button>
              ))}
            </div>
            <div className="max-h-56 overflow-y-auto p-2.5">
              {tab === "skills" && (skills.length === 0 ? (
                <div className="px-2 py-1 text-xs text-paper-500">(no skills)</div>
              ) : skills.map((s) => (
                <div key={s.name} className="mb-2 rounded-lg border border-paper-300/70 bg-paper-50 p-2.5">
                  <div className="font-mono text-xs text-clay-600">{s.name}</div>
                  <div className="mt-1 text-xs leading-relaxed text-paper-600">{s.description}</div>
                </div>
              )))}
              {tab === "mcp" && (mcp.length === 0 ? (
                <div className="px-2 py-1 text-xs text-paper-500">(no MCP servers connected)</div>
              ) : mcp.map((srv) => (
                <div key={srv.name} className="mb-3">
                  <div className="mb-1.5 font-mono text-xs text-clay-600">{srv.name}</div>
                  {srv.tools.length === 0 ? (
                    <div className="px-2 text-xs text-paper-500">(no tools)</div>
                  ) : srv.tools.map((t) => (
                    <div key={t.name} className="mb-1.5 rounded-lg border border-paper-300/70 bg-paper-50 px-2.5 py-2">
                      <div className="font-mono text-xs text-paper-800">{t.name}</div>
                      {t.description && (
                        <div className="mt-0.5 truncate text-xs text-paper-600" title={t.description}>{t.description}</div>
                      )}
                    </div>
                  ))}
                </div>
              )))}
            </div>
          </>
        )}
      </div>
    </aside>
  );
}

function SessionList({ sm }: { sm: SessionManager }) {
  const { sessions, currentId, switchTo, newSession, removeSession } = sm;
  const sorted = [...sessions].sort((a, b) => b.last_activity - a.last_activity);
  return (
    <div className="flex flex-col border-b border-paper-300/70">
      <div className="flex items-center justify-between px-3 py-2">
        <span className="px-1 text-xs uppercase tracking-wide text-paper-500">会话</span>
        <button
          className="rounded-md border border-paper-300 px-2 py-1 text-xs font-medium text-paper-700 transition hover:bg-paper-200"
          onClick={() => newSession()}
          title="new session"
        >+ 新建</button>
      </div>
      <div className="max-h-60 overflow-y-auto px-2 pb-2.5">
        {sorted.length === 0 && (
          <div className="px-2 py-1 text-xs text-paper-500">(no sessions)</div>
        )}
        {sorted.map((s: SessionMeta) => {
          const active = s.session_id === currentId;
          return (
            <div key={s.session_id}
                 className={`group mb-1 flex cursor-pointer items-center gap-1.5 rounded-lg px-2.5 py-2 transition ${
                   active ? "bg-clay-100 text-paper-900" : "text-paper-700 hover:bg-paper-200"
                 }`}
                 onClick={() => switchTo(s.session_id)}>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px]">{s.title || "(new session)"}</div>
                <div className="truncate font-mono text-[10px] text-paper-500">{s.session_id.slice(0, 8)} · {s.history_len} msg</div>
              </div>
              <button
                className="opacity-0 transition group-hover:opacity-100 hover:text-red-500"
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
