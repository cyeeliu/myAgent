"use client";
// Live-activity indicator for the /ws shell. Reads chatStore flags directly
// (no reducer) — thinking/processing/streaming map to the same visual states
// as the legacy StatusBar.
import { useChatStore } from "../../lib/stores/chatStore";

function Spinner({ className }: { className: string }) {
  return (
    <span
      className={`inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-transparent ${className}`}
      style={{ borderTopColor: "currentColor", borderRightColor: "currentColor" }}
    />
  );
}

export function StatusBar() {
  const isProcessing = useChatStore((s) => s.isProcessing);
  const isThinking = useChatStore((s) => s.isThinking);
  const hasQuestion = useChatStore((s) => !!s.pendingQuestion && !s.pendingQuestion.resolved);
  if (!isProcessing && !hasQuestion) return null;

  const kind = hasQuestion ? "permission" : isThinking ? "thinking" : "replying";
  const accent =
    kind === "replying" ? "text-clay-600"
    : kind === "permission" ? "text-amber-600"
    : "text-paper-600";
  const label = kind === "permission" ? "等待授权" : kind === "thinking" ? "思考中" : "正在回复";

  return (
    <div className="flex items-center gap-2 border-t border-paper-300/70 bg-paper-100 px-6 py-2 text-xs">
      <span className={accent}><Spinner className={accent} /></span>
      <span className={accent}>{label}</span>
    </div>
  );
}
