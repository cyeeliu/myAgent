"use client";
// Session sidebar for the /ws shell. Lists sessions from useSessionStore with
// new / switch / delete, plus view tabs (chat / tools / skills / agents / config)
// at the bottom. Reconciles with the gateway on mount and polls.
import { useEffect } from "react";
import { useSessionStore } from "../../lib/stores/sessionStore";
import type { ShellView } from "../../app/shell/page";

const VIEWS: Array<{ key: ShellView; label: string }> = [
  { key: "chat", label: "对话" },
  { key: "tools", label: "工具" },
  { key: "skills", label: "技能" },
  { key: "agents", label: "智能体" },
  { key: "config", label: "配置" },
];

function Spark({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 12 12" fill="currentColor" aria-hidden>
      <path d="M6 0l1.6 3.4L11 5l-3.4 1.6L6 10 4.4 6.6 1 5l3.4-1.6z" />
    </svg>
  );
}

export function SessionSidebar({
  view,
  setView,
}: {
  view: ShellView;
  setView: (v: ShellView) => void;
}) {
  const sessions = useSessionStore((s) => s.sessions);
  const currentId = useSessionStore((s) => s.currentId);
  const refresh = useSessionStore((s) => s.refresh);
  const create = useSessionStore((s) => s.create);
  const remove = useSessionStore((s) => s.remove);
  const setCurrent = useSessionStore((s) => s.setCurrent);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
  }, [refresh]);

  // Ensure there's always a current session.
  useEffect(() => {
    if (!currentId && sessions.length === 0) {
      create().catch(() => {});
    } else if (!currentId && sessions.length > 0) {
      setCurrent(sessions[0].session_id);
    }
  }, [currentId, sessions, create, setCurrent]);

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-paper-300/70 bg-paper-150">
      <div className="flex items-center justify-between px-4 py-3.5">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-clay-500 text-white">
            <Spark size={13} />
          </span>
          <span className="text-[15px] font-semibold text-paper-900">myAgent</span>
        </div>
        <button
          onClick={() => create()}
          className="rounded-lg border border-paper-300 px-2 py-1 text-xs text-paper-700 transition hover:bg-paper-200"
          title="新建会话"
        >+</button>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-4">
        {sessions.length === 0 ? (
          <p className="px-2 py-4 text-xs text-paper-500">暂无会话</p>
        ) : (
          sessions.map((m) => (
            <div
              key={m.session_id}
              onClick={() => setCurrent(m.session_id)}
              className={`group mb-0.5 flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-sm transition ${
                m.session_id === currentId
                  ? "bg-clay-100 text-paper-900"
                  : "text-paper-700 hover:bg-paper-200"
              }`}
            >
              <span className="flex-1 truncate">{m.title || "(untitled)"}</span>
              <button
                onClick={(e) => { e.stopPropagation(); remove(m.session_id); }}
                className="hidden text-paper-500 hover:text-red-500 group-hover:block"
                title="删除"
              >×</button>
            </div>
          ))
        )}
      </div>
      <nav className="flex flex-col gap-0.5 border-t border-paper-300/70 px-2 py-2">
        {VIEWS.map((v) => (
          <button
            key={v.key}
            onClick={() => setView(v.key)}
            className={`rounded-lg px-2.5 py-1.5 text-left text-sm transition ${
              view === v.key
                ? "bg-clay-100 text-clay-700"
                : "text-paper-600 hover:bg-paper-200"
            }`}
          >
            {v.label}
          </button>
        ))}
      </nav>
    </aside>
  );
}
