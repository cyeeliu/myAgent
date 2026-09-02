"use client";
// Input area for the /ws shell. Send / interrupt / answer-pending-question.
import { useCallback, useRef, useState } from "react";
import { useChatStore } from "../../lib/stores/chatStore";

export function ChatInputArea({
  ready,
  send,
  interrupt,
  answer,
}: {
  ready: boolean;
  send: (text: string) => Promise<void>;
  interrupt: () => Promise<void>;
  answer: (allow: boolean) => Promise<void>;
}) {
  const [input, setInput] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);
  const isProcessing = useChatStore((s) => s.isProcessing);
  const pendingQuestion = useChatStore((s) => s.pendingQuestion);

  const grow = useCallback(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 220) + "px";
  }, []);

  const submit = useCallback(() => {
    const body = input.trim();
    if (!body || !ready || isProcessing) return;
    void send(body);
    setInput("");
    requestAnimationFrame(() => { if (taRef.current) taRef.current.style.height = "auto"; });
  }, [input, ready, isProcessing, send]);

  const onKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
  }, [submit]);

  return (
    <div className="border-t border-paper-300/70 bg-paper-100/80 backdrop-blur">
      <div className="mx-auto max-w-3xl px-6 py-4">
        {pendingQuestion && !pendingQuestion.resolved && (
          <div className="mb-2 flex items-center justify-end gap-2">
            <button
              onClick={() => answer(false)}
              className="rounded-lg border border-paper-300 px-3 py-1.5 text-xs text-paper-700 hover:bg-paper-200"
            >拒绝</button>
            <button
              onClick={() => answer(true)}
              className="rounded-lg bg-clay-500 px-3 py-1.5 text-xs text-white hover:bg-clay-600"
            >允许</button>
          </div>
        )}
        <div className="flex items-end gap-2 rounded-2xl border border-paper-300 bg-paper-50 px-4 py-2.5 shadow-composer transition focus-within:border-clay-400">
          <textarea
            ref={taRef}
            rows={1}
            value={input}
            onChange={(e) => { setInput(e.target.value); grow(); }}
            onKeyDown={onKeyDown}
            placeholder={!ready ? "连接中…" : isProcessing ? "回合进行中…" : "给 myAgent 发消息…"}
            className="max-h-[220px] flex-1 resize-none bg-transparent py-1.5 text-[0.95rem] leading-relaxed text-paper-900 outline-none placeholder:text-paper-500"
          />
          {isProcessing ? (
            <button
              onClick={() => interrupt()}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-paper-300 text-paper-700 transition hover:bg-paper-200"
              title="停止"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden>
                <rect x="3" y="3" width="10" height="10" rx="2" />
              </svg>
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={!ready || !input.trim()}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-clay-500 text-white transition hover:bg-clay-600 disabled:bg-paper-300 disabled:text-paper-500"
              title="发送"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M3 8h10M9 4l4 4-4 4" />
              </svg>
            </button>
          )}
        </div>
        <p className="mt-2 text-center text-[11px] text-paper-500">
          myAgent 可能会犯错，请核实重要信息。Enter 发送 · Shift+Enter 换行
        </p>
      </div>
    </div>
  );
}
