"use client";
import { useEffect, useRef, useState } from "react";
import { ToolCard } from "./ToolCard";
import type { Item } from "../lib/reducer";

/**
 * Collapsible container for a run of consecutive tool calls within one turn.
 * Many tool calls used to each get their own block and flood the chat. Now a
 * run of ≥2 consecutive tools collapses into one compact header ("N 次工具调用"
 * + a tool-name summary); expanding reveals a scrollable list of the individual
 * ToolCards.
 *
 * Defaults to open. While open it auto-scrolls to bottom as new tool cards or
 * results arrive — same atBottom-gated logic as the outer chat panel — so the
 * latest output stays in view without yanking the scroll when the user reads
 * higher up.
 */
export function ToolGroup({ tools }: { tools: Item[] }) {
  const pending = tools.some((t) => t.kind === "tool" && t.result === undefined && !t.blocked);
  const [open, setOpen] = useState(true);

  const scrollRef = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);
  const scrollToBottom = () => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  };
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 40);
  };

  // Signature that captures new tools AND result growth, so the auto-scroll
  // fires when meaningful content changes (not on every parent re-render).
  const sig = tools
    .map((t) => (t.kind === "tool" ? `${t.name}|${t.result !== undefined}|${(t.result ?? "").length}` : ""))
    .join(";");

  useEffect(() => {
    if (open && atBottom) scrollToBottom();
  }, [sig, open, atBottom]);

  const names = Array.from(new Set(
    tools.map((t) => (t.kind === "tool" ? t.name : "")).filter(Boolean),
  ));
  const summary =
    names.length <= 3
      ? names.join(", ")
      : `${names.slice(0, 3).join(", ")} +${names.length - 3}`;
  const done = tools.filter((t) => t.kind === "tool" && t.result !== undefined).length;

  return (
    <div className="overflow-hidden rounded-xl border border-paper-300 bg-paper-50/60 text-sm shadow-soft">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left transition hover:bg-paper-150"
      >
        <span className={`h-2 w-2 shrink-0 rounded-full ${
          pending ? "bg-clay-500 animate-pulse" : "bg-emerald-500"
        }`} />
        <span className="font-medium text-[13px] text-paper-900">
          {tools.length} 次工具调用
        </span>
        <span className="truncate font-mono text-xs text-paper-500">{summary}</span>
        {pending ? (
          <span className="text-xs text-paper-500">运行中…</span>
        ) : (
          <span className="text-xs text-paper-400">{done} 完成</span>
        )}
        <span className="ml-auto text-xs text-paper-500">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="max-h-[60vh] space-y-3 overflow-y-auto border-t border-paper-300/70 p-3"
        >
          {tools.map((t, i) =>
            t.kind === "tool" ? (
              <ToolCard
                key={i}
                name={t.name}
                input={t.input}
                result={t.result}
                blocked={t.blocked}
              />
            ) : null,
          )}
        </div>
      )}
    </div>
  );
}
