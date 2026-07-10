"use client";
// Message list for the /ws shell. Renders chatStore.messages; tool executions
// attached to the preceding assistant turn render as inline cards. Reuses the
// legacy Markdown component for assistant content.
import { useEffect, useRef } from "react";
import { useChatStore } from "../../lib/stores/chatStore";
import { Markdown } from "../Markdown";

function Spark() {
  return (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-clay-500 text-white shadow-soft">
      <svg width="15" height="15" viewBox="0 0 12 12" fill="currentColor" aria-hidden>
        <path d="M6 0l1.6 3.4L11 5l-3.4 1.6L6 10 4.4 6.6 1 5l3.4-1.6z" />
      </svg>
    </span>
  );
}

function ToolRow({ name, input, result, status }: {
  name: string; input: unknown; result?: string; status: string;
}) {
  return (
    <div className="my-2 rounded-lg border border-paper-300 bg-paper-50 px-3 py-2 text-xs">
      <div className="flex items-center gap-2 font-medium text-paper-800">
        <span className={`h-1.5 w-1.5 rounded-full ${
          status === "completed" ? "bg-emerald-500"
          : status === "error" ? "bg-red-500"
          : "bg-clay-400 animate-pulse"}`} />
        {name}
      </div>
      {input != null && (
        <pre className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-words text-paper-600">
{typeof input === "string" ? input : JSON.stringify(input, null, 2)}
        </pre>
      )}
      {result != null && (
        <pre className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-words text-paper-700">
{result}
        </pre>
      )}
    </div>
  );
}

export function ChatMessageList() {
  const messages = useChatStore((s) => s.messages);
  const toolExecutions = useChatStore((s) => s.toolExecutions);
  const toolOrder = useChatStore((s) => s.toolExecutionOrder);
  const pendingQuestion = useChatStore((s) => s.pendingQuestion);

  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, pendingQuestion]);

  const tools = toolOrder.map((id) => toolExecutions.get(id)).filter(Boolean);

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl px-6 py-10">
        {messages.length === 0 && (
          <div className="flex h-[60vh] flex-col items-center justify-center text-center">
            <h1 className="font-assistant text-[2rem] font-medium text-paper-950">
              有什么可以帮忙的？
            </h1>
            <p className="mt-3 max-w-md text-sm text-paper-600">
              一个会调用工具、读写文件、运行命令的智能体。
            </p>
          </div>
        )}
        {messages.map((m) => {
          if (m.role === "user") {
            return (
              <div key={m.id} className="mb-9 flex justify-end">
                <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-clay-500 px-4 py-2.5 text-[0.95rem] leading-relaxed text-white shadow-soft">
                  {m.content}
                </div>
              </div>
            );
          }
          const text = m.content.replace(/\s+$/, "");
          return (
            <div key={m.id} className="mb-9 flex gap-4">
              <Spark />
              <div className="min-w-0 flex-1 pt-1">
                <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-paper-500">myAgent</div>
                {text.trim() && (
                  <div className="font-assistant text-paper-900">
                    <Markdown content={text} />
                  </div>
                )}
                {m.isStreaming && !text.trim() && (
                  <div className="text-paper-500">…</div>
                )}
              </div>
            </div>
          );
        })}
        {tools.length > 0 && (
          <div className="mb-9 flex gap-4">
            <Spark />
            <div className="min-w-0 flex-1 pt-1">
              {tools.map((t) => (
                <ToolRow
                  key={t!.toolCallId}
                  name={t!.toolCall.name}
                  input={t!.toolCall.input}
                  result={t!.result?.result}
                  status={t!.status}
                />
              ))}
            </div>
          </div>
        )}
        {pendingQuestion && !pendingQuestion.resolved && (
          <div className="mb-9 flex gap-4">
            <Spark />
            <div className="min-w-0 flex-1 pt-1">
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-sm text-amber-800">
                <div className="font-medium">{pendingQuestion.question}</div>
                {pendingQuestion.detail && (
                  <div className="mt-1 text-amber-700">{pendingQuestion.detail}</div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
