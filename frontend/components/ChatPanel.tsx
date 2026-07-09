"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useAgentTransport } from "../lib/useAgentTransport";
import { ToolCard } from "./ToolCard";
import { ToolGroup } from "./ToolGroup";
import { PermissionCard } from "./PermissionCard";
import { StatusBar } from "./StatusBar";
import { Markdown } from "./Markdown";
import { SlashCommandMenu } from "./SlashCommandMenu";
import {
  reduce, reduceUser, initialState,
  initialActivity, applyLiveActivity,
  type Item, type Activity,
} from "../lib/reducer";
import { sessionWorkerAlive } from "../lib/sessions";
import type { AgentEvent } from "../lib/types";

const THINKING = { kind: "thinking" as const, toolName: null, toolCount: 0, round: 1 };

function Spark({ size = 14, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 12 12" fill="currentColor" className={className} aria-hidden>
      <path d="M6 0l1.6 3.4L11 5l-3.4 1.6L6 10 4.4 6.6 1 5l3.4-1.6z" />
    </svg>
  );
}

function AssistantMark() {
  return (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-clay-500 text-white shadow-soft">
      <Spark size={15} />
    </span>
  );
}

type RenderNode =
  | { tag: "single"; item: Item; i: number }
  | { tag: "group"; tools: Item[]; i: number };

/** Group consecutive tool items into one node so they render inside a single
 * collapsible ToolGroup instead of one block per call. A lone tool stays a
 * single node (rendered as its own ToolCard) — only runs of ≥2 collapse. */
function groupItems(items: Item[]): RenderNode[] {
  const nodes: RenderNode[] = [];
  let i = 0;
  while (i < items.length) {
    if (items[i].kind === "tool") {
      const start = i;
      while (i < items.length && items[i].kind === "tool") i++;
      const tools = items.slice(start, i);
      if (tools.length >= 2) {
        nodes.push({ tag: "group", tools, i: start });
      } else {
        nodes.push({ tag: "single", item: tools[0], i: start });
      }
    } else {
      nodes.push({ tag: "single", item: items[i], i });
      i++;
    }
  }
  return nodes;
}

const SUGGESTIONS = [
  "帮我读懂这个项目的架构",
  "写一个单元测试",
  "重构这段函数",
  "解释最近的 git 提交",
];

export function ChatPanel({ sessionId }: { sessionId: string | null }) {
  const [items, setItems] = useState<Item[]>([]);
  const [activity, setActivity] = useState<Activity>(initialActivity);
  const [input, setInput] = useState("");
  const [showSlashMenu, setShowSlashMenu] = useState(false);
  const stateRef = useRef(initialState());

  const scrollRef = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 56);
  }, []);

  useEffect(() => { if (atBottom) scrollToBottom(); }, [items, atBottom, scrollToBottom]);

  const [prevSid, setPrevSid] = useState(sessionId);
  if (prevSid !== sessionId) {
    setPrevSid(sessionId);
    stateRef.current = initialState();
    setItems([]);
    setActivity(initialActivity);
    setInput("");
    setAtBottom(true);
  }

  const onEvent = useCallback((e: AgentEvent) => {
    const next = reduce(stateRef.current, e);
    stateRef.current = next;
    setItems(next.items);
    if (!(e.payload as any)?.replay) {
      setActivity((prev) => applyLiveActivity(prev, e));
    }
  }, []);

  const { transport, connected, send, interrupt } = useAgentTransport(sessionId, onEvent);

  useEffect(() => {
    if (!connected || !sessionId) return;
    let cancelled = false;
    sessionWorkerAlive(sessionId).then((alive) => {
      if (cancelled || !alive) return;
      setActivity((prev) => prev.inFlight ? prev : {
        inFlight: true,
        status: prev.status.kind === "idle" ? THINKING : prev.status,
      });
    });
    return () => { cancelled = true; };
  }, [sessionId, connected]);

  const taRef = useRef<HTMLTextAreaElement>(null);
  const grow = useCallback(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 220) + "px";
  }, []);

  const submit = (text?: string) => {
    const body = (text ?? input).trim();
    if (!body || !connected || activity.inFlight) return;
    stateRef.current = reduceUser(stateRef.current, body);
    setItems(stateRef.current.items);
    setActivity({ status: THINKING, inFlight: true });
    send({ type: "user_message", text: body });
    setInput("");
    setShowSlashMenu(false);
    requestAnimationFrame(() => { if (taRef.current) taRef.current.style.height = "auto"; });
  };

  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    setInput(value);
    grow();

    // Show slash menu when typing "/" at start of input
    if (value === "/") {
      setShowSlashMenu(true);
    } else if (showSlashMenu && !value.startsWith("/")) {
      setShowSlashMenu(false);
    }
  }, [grow, showSlashMenu]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    } else if (e.key === "Escape" && showSlashMenu) {
      e.preventDefault();
      setShowSlashMenu(false);
    }
  }, [submit, showSlashMenu]);

  const handleSlashCommandSelect = useCallback((command: string) => {
    setInput(command + " ");
    setShowSlashMenu(false);
    taRef.current?.focus();
  }, []);

  const respondPermission = (rid: string, allow: boolean) => {
    send({ type: "permission_response", request_id: rid, allow });
    setItems((p) => p.map((it) =>
      it.kind === "permission" && (it as any).rid === rid ? { ...it, resolved: true } as Item : it));
  };

  const inFlight = activity.inFlight;
  const empty = items.length === 0;

  return (
    <div className="flex h-screen flex-col bg-paper-100">
      <header className="flex items-center justify-between border-b border-paper-300/70 px-6 py-3">
        <div className="flex items-center gap-2.5">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-clay-500 text-white">
            <Spark size={13} />
          </span>
          <span className="text-[15px] font-semibold text-paper-900">myAgent</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1.5 text-xs text-paper-600">
            <span className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-emerald-500" : "bg-paper-400"}`} />
            {connected ? (transport ?? "live") : "connecting"}
          </span>
          <button
            className="rounded-lg border border-paper-300 px-2.5 py-1 text-xs font-medium text-paper-700 transition hover:bg-paper-200 disabled:opacity-40"
            onClick={interrupt} disabled={!inFlight}
          >停止</button>
        </div>
      </header>

      <div className="relative flex-1">
        <div ref={scrollRef} onScroll={onScroll} className="absolute inset-0 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-6">
            {empty ? (
              <div className="flex h-[68vh] flex-col items-center justify-center text-center">
                <span className="mb-5 flex h-14 w-14 items-center justify-center rounded-2xl bg-clay-500 text-white shadow-soft">
                  <Spark size={26} />
                </span>
                <h1 className="font-assistant text-[2rem] font-medium leading-tight text-paper-950">
                  有什么可以帮忙的？
                </h1>
                <p className="mt-3 max-w-md text-sm leading-relaxed text-paper-600">
                  一个会调用工具、读写文件、运行命令的智能体。
                </p>
                <div className="mt-7 grid w-full max-w-lg grid-cols-2 gap-2.5">
                  {SUGGESTIONS.map((s) => (
                    <button key={s}
                      onClick={() => submit(s)}
                      disabled={!connected || inFlight}
                      className="rounded-xl border border-paper-300 bg-paper-50 px-3.5 py-2.5 text-left text-[13px] text-paper-700 transition hover:border-clay-300 hover:bg-clay-50 hover:text-paper-900 disabled:opacity-40">
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="py-10">
                {groupItems(items).map((node) => {
                  if (node.tag === "group") {
                    return (
                      <div key={`g${node.i}`} className="mb-9 flex gap-4">
                        <AssistantMark />
                        <div className="min-w-0 flex-1 pt-1">
                          <ToolGroup tools={node.tools} />
                        </div>
                      </div>
                    );
                  }
                  const it = node.item;
                  const i = node.i;
                  if (it.kind === "user") {
                    return (
                      <div key={i} className="mb-9 flex justify-end">
                        <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-clay-500 px-4 py-2.5 text-[0.95rem] leading-relaxed text-white shadow-soft">
                          {it.text}
                        </div>
                      </div>
                    );
                  }
                  if (it.kind === "assistant") {
                    const text = it.text.replace(/\s+$/, "");
                    if (!text.trim()) return null;
                    return (
                      <div key={i} className="mb-9 flex gap-4">
                        <AssistantMark />
                        <div className="min-w-0 flex-1 pt-1">
                          <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-paper-500">myAgent</div>
                          <div className="font-assistant text-paper-900">
                            <Markdown content={text} />
                          </div>
                        </div>
                      </div>
                    );
                  }
                  if (it.kind === "tool") {
                    return (
                      <div key={i} className="mb-9 flex gap-4">
                        <AssistantMark />
                        <div className="min-w-0 flex-1 pt-1">
                          <ToolCard name={it.name} input={it.input} result={it.result} blocked={it.blocked} />
                        </div>
                      </div>
                    );
                  }
                  if (it.kind === "permission") {
                    return !it.resolved ? (
                      <div key={i} className="mb-9 flex gap-4">
                        <AssistantMark />
                        <div className="min-w-0 flex-1 pt-1">
                          <PermissionCard reason={it.reason} detail={it.detail} onRespond={(a) => respondPermission(it.rid, a)} />
                        </div>
                      </div>
                    ) : null;
                  }
                  if (it.kind === "notice") {
                    return <div key={i} className="my-4 text-center text-xs text-paper-500">{it.text}</div>;
                  }
                  if (it.kind === "error") {
                    return (
                      <div key={i} className="my-4 rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-700">
                        {it.text}
                      </div>
                    );
                  }
                  return null;
                })}
              </div>
            )}
          </div>
        </div>
        {!atBottom && (
          <button
            onClick={scrollToBottom}
            className="absolute bottom-6 right-6 z-10 flex h-10 w-10 items-center justify-center rounded-full border border-paper-300 bg-paper-50 text-paper-700 shadow-composer transition hover:bg-paper-200"
            title="滚动到底部"
          >
            <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M4 6l4 4 4-4" />
            </svg>
          </button>
        )}
      </div>

      <StatusBar status={activity.status} />

      <div className="border-t border-paper-300/70 bg-paper-100/80 backdrop-blur">
        <div className="mx-auto max-w-3xl px-6 py-4">
          <div className="relative">
            <div className="flex items-end gap-2 rounded-2xl border border-paper-300 bg-paper-50 px-4 py-2.5 shadow-composer transition focus-within:border-clay-400">
              <textarea
                ref={taRef}
                rows={1}
                value={input}
                onChange={handleInputChange}
                onKeyDown={handleKeyDown}
                placeholder={!connected ? "连接中…" : inFlight ? "回合进行中…" : "给 myAgent 发消息…"}
                className="max-h-[220px] flex-1 resize-none bg-transparent py-1.5 text-[0.95rem] leading-relaxed text-paper-900 outline-none placeholder:text-paper-500"
              />
              <button
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-clay-500 text-white transition hover:bg-clay-600 disabled:cursor-not-allowed disabled:bg-paper-300 disabled:text-paper-500"
                onClick={() => submit()}
                disabled={!connected || inFlight || !input.trim()}
                title="发送"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M3 8h10M9 4l4 4-4 4" />
                </svg>
              </button>
            </div>
            {showSlashMenu && (
              <SlashCommandMenu
                onSelect={handleSlashCommandSelect}
                onClose={() => setShowSlashMenu(false)}
              />
            )}
          </div>
          <p className="mt-2 text-center text-[11px] text-paper-500">
            myAgent 可能会犯错，请核实重要信息。Enter 发送 · Shift+Enter 换行
          </p>
        </div>
      </div>
    </div>
  );
}
