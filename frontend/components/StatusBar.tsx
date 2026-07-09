"use client";
import type { Status } from "../lib/reducer";

// Live-activity indicator pinned above the composer. Surfaces what the agent
// is doing right now. Driven by reducer `status`. Hidden when idle.

const LABELS: Record<Status["kind"], string> = {
  idle: "",
  thinking: "思考中",
  replying: "正在回复",
  tool: "调用工具",
  permission: "等待授权",
};

function Spinner({ className }: { className: string }) {
  return (
    <span className={`inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-transparent ${className}`}
          style={{ borderTopColor: "currentColor", borderRightColor: "currentColor" }} />
  );
}

function Dot() {
  return <span className="inline-block h-2.5 w-2.5 animate-pulse rounded-full bg-current" />;
}

export function StatusBar({ status }: { status: Status }) {
  if (status.kind === "idle") return null;

  const accent =
    status.kind === "replying" ? "text-clay-600"
    : status.kind === "tool" ? "text-clay-500"
    : status.kind === "permission" ? "text-amber-600"
    : "text-paper-600"; // thinking

  const label = LABELS[status.kind];
  const toolSuffix = status.kind === "tool" && status.toolName ? ` ${status.toolName}` : "";
  const roundSuffix = status.toolCount > 0 ? ` · 第 ${status.round} 轮 · ${status.toolCount} 次工具` : "";

  return (
    <div className="flex items-center gap-2 border-t border-paper-300/70 bg-paper-100 px-6 py-2 text-xs">
      <span className={accent}>
        {status.kind === "permission" ? <Dot /> : <Spinner className={accent} />}
      </span>
      <span className={accent}>{label}{toolSuffix}</span>
      {roundSuffix && <span className="text-paper-500">{roundSuffix}</span>}
    </div>
  );
}
