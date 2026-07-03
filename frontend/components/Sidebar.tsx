"use client";
import { useEffect, useState } from "react";

const GATEWAY = process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8000";

type Skill = { name: string; description: string };
type Task = { id: string; subject: string; status: string };

// Side panel: read-only views of the agent's live state (spec §6 sidebar).
// Skills/tasks/memories are fetched from the gateway's dot-dir endpoints and
// refreshed every few seconds.
export function Sidebar() {
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
