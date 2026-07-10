"use client";
// Tool panel — live tool executions from the chat store (read-only view).
import { useChatStore } from "../../lib/stores/chatStore";

export function ToolPanel() {
  const toolExecutions = useChatStore((s) => s.toolExecutions);
  const toolOrder = useChatStore((s) => s.toolExecutionOrder);
  const tools = toolOrder.map((id) => toolExecutions.get(id)).filter(Boolean);

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <h2 className="mb-6 text-lg font-semibold text-paper-900">工具调用</h2>
      {tools.length === 0 ? (
        <p className="text-sm text-paper-500">本回合暂无工具调用</p>
      ) : (
        <ul className="space-y-2">
          {tools.map((t) => (
            <li key={t!.toolCallId} className="rounded-xl border border-paper-300 bg-paper-50 p-4">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-paper-900">{t!.toolCall.name}</span>
                <span className={`text-xs ${
                  t!.status === "completed" ? "text-emerald-600"
                  : t!.status === "error" ? "text-red-500"
                  : "text-clay-500"}`}>
                  {t!.status}
                </span>
              </div>
              {t!.toolCall.input != null && (
                <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs text-paper-600">
{typeof t!.toolCall.input === "string" ? t!.toolCall.input : JSON.stringify(t!.toolCall.input, null, 2)}
                </pre>
              )}
              {t!.result?.result != null && (
                <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs text-paper-700">
{t!.result.result}
                </pre>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
