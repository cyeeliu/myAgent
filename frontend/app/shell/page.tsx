"use client";
// Multi-panel shell (new /ws architecture). Lives at /shell so the legacy
// app/page.tsx (event-frame WS) keeps working during the migration. Layout:
//   [SessionSidebar + view tabs] [ main: active view, full-bleed ]
//
// The WS connection is owned HERE (not in ChatPanel) so it stays alive across
// view switches — switching to the tools/config panel must not drop the live
// event stream. useWebSocket binds webClient → zustand stores for the current
// session; side panels fire method-routed requests directly via webRequest.
import { useEffect, useState } from "react";
import { SessionSidebar } from "../../components/shell/SessionSidebar";
import { ChatPanel } from "../../components/shell/ChatPanel";
import { ToolPanel } from "../../components/shell/ToolPanel";
import { ConfigPanel } from "../../components/shell/ConfigPanel";
import { SkillPanel } from "../../components/shell/SkillPanel";
import { AgentPanel } from "../../components/shell/AgentPanel";
import { useSessionStore } from "../../lib/stores/sessionStore";
import { useChatStore } from "../../lib/stores/chatStore";
import { useWebSocket } from "../../lib/useWebSocket";

export type ShellView = "chat" | "tools" | "config" | "skills" | "agents";

export default function ShellPage() {
  const [view, setView] = useState<ShellView>("chat");
  const sessionId = useSessionStore((s) => s.currentId);
  const clear = useChatStore((s) => s.clear);
  const ws = useWebSocket(sessionId);

  // Reset the conversation when the bound session changes.
  useEffect(() => { clear(); }, [sessionId, clear]);

  return (
    <div className="flex h-screen bg-paper-100 text-paper-900">
      <SessionSidebar view={view} setView={setView} />
      <main className="flex-1 overflow-hidden">
        {view === "chat" && <ChatPanel ws={ws} />}
        {view === "tools" && <div className="h-full overflow-y-auto"><ToolPanel /></div>}
        {view === "skills" && <div className="h-full overflow-y-auto"><SkillPanel /></div>}
        {view === "agents" && <div className="h-full overflow-y-auto"><AgentPanel /></div>}
        {view === "config" && <div className="h-full overflow-y-auto"><ConfigPanel /></div>}
      </main>
    </div>
  );
}
