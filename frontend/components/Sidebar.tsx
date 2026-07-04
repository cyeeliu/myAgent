"use client";
import { useEffect, useState } from "react";
import type { SessionManager } from "../lib/useSessionManager";
import type { SessionMeta } from "../lib/sessions";

const GATEWAY = process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8000";

type Skill = { name: string; description: string };
type Task = { id: string; subject: string; status: string };

// Side panel: session switcher (top) + read-only views of the agent's live
// state (skills/tasks/memory). Skills/tasks/memories are fetched from the
// gateway's dot-dir endpoints and refreshed every few seconds.
export function Sidebar({ sm }: { sm: SessionManager }) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [memories, setMemories] = useState("");
  const [tab, setTab] = useState<"skills" | "tasks" | "memory">("skills");

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const [s, t, m] = await Promise.all([
          fetch(`${GATEWAY}/api/skills`).then((r) => r.json()),
          fetch(`${GATEWAY}/api/tasks`).then((r) => r.json()),
          fetch(`${GATEWAY}/api/memories`).then((r) => r.json()),
        ]);
        if (cancelled) return;
        setSkills(Array.isArray(s) ? s : []);
        setTasks(Array.isArray(t) ? t : []);
        setMemories(m?.text || "");
      } catch { /* gateway not up yet */ }
    }
    refresh();
    const id = setInterval(refresh, 4000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  return (
    <aside className="flex w-72 flex-col border-r border-zinc-800 bg-zinc-950 text-sm">
      <SessionList sm={sm} />
      <div className="flex border-b border-zinc-800">
        {(["skills", "tasks", "memory"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
                  className={`flex-1 px-2 py-2 ${tab === t ? "bg-zinc-800 text-cyan-300" : "text-zinc-400"}`}>
            {t}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {tab === "skills" && skills.map((s) => (
          <div key={s.name} className="mb-2 rounded bg-zinc-900 p-2">
            <div className="font-mono text-cyan-400">{s.name}</div>
            <div className="text-xs text-zinc-400">{s.description}</div>
          </div>
        ))}
        {tab === "tasks" && tasks.map((t) => (
          <div key={t.id} className="mb-2 rounded bg-zinc-900 p-2">
            <div className="text-zinc-200">{t.subject}</div>
            <div className="text-xs text-zinc-500">{t.status}</div>
          </div>
        ))}
        {tab === "memory" && (
          <pre className="whitespace-pre-wrap text-xs text-zinc-400">{memories || "(no memories)"}</pre>
        )}
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
