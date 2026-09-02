"use client";
// ChatPanel for the /ws shell — purely presentational. The WS connection is
// owned by ShellPage (so it stays alive across view switches); this component
// just reads the chat store and renders the header + message list + input.
import { useChatStore } from "../../lib/stores/chatStore";
import { ChatMessageList } from "./ChatMessageList";
import { ChatInputArea } from "./ChatInputArea";
import { StatusBar } from "./StatusBar";
import type { UseWebSocket } from "../../lib/useWebSocket";

export function ChatPanel({ ws }: { ws: UseWebSocket }) {
  const clear = useChatStore((s) => s.clear);
  // Conversation reset on session switch is handled in ShellPage (it owns the
  // session id); here we only render. `clear` is referenced to keep the store
  // wiring stable if future per-panel resets are needed.
  void clear;

  return (
    <div className="flex h-full flex-col bg-paper-100">
      <header className="flex items-center justify-between border-b border-paper-300/70 px-6 py-3">
        <div className="flex items-center gap-2.5">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-clay-500 text-white">
            <svg width="13" height="13" viewBox="0 0 12 12" fill="currentColor" aria-hidden>
              <path d="M6 0l1.6 3.4L11 5l-3.4 1.6L6 10 4.4 6.6 1 5l3.4-1.6z" />
            </svg>
          </span>
          <span className="text-[15px] font-semibold text-paper-900">myAgent</span>
        </div>
        <span className="flex items-center gap-1.5 text-xs text-paper-600">
          <span className={`h-1.5 w-1.5 rounded-full ${ws.ready ? "bg-emerald-500" : "bg-paper-400"}`} />
          {ws.ready ? "live" : ws.state === "reconnecting" ? "重连中" : "connecting"}
        </span>
      </header>
      <ChatMessageList />
      <StatusBar />
      <ChatInputArea ready={ws.ready} send={ws.send} interrupt={ws.interrupt} answer={ws.answer} />
    </div>
  );
}
