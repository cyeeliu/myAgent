"use client";
import { useState } from "react";
import { ChatPanel } from "../components/ChatPanel";
import { Sidebar } from "../components/Sidebar";
import { AgentEditor } from "../components/AgentEditor";
import { ModelConfigPanel } from "../components/ModelConfigPanel";
import { useSessionManager } from "../lib/useSessionManager";

// Top-level layout: sidebar (3-column nav: 会话/智能体/模型) and the right
// panel switched by `view`. The session manager owns the session list + current
// selection and persists them to localStorage so a page refresh reattaches to
// the same session. `selectedAgent` is the agent currently open in AgentEditor
// (null = new-agent form); lifted here so Sidebar's AgentList and the right
// AgentEditor share selection without sibling dark channels.
export type View = "sessions" | "agents" | "model";

export default function Page() {
  const sm = useSessionManager();
  const [view, setView] = useState<View>("sessions");
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  return (
    <div className="flex h-screen bg-paper-100 text-paper-900">
      <Sidebar
        sm={sm}
        view={view}
        setView={setView}
        selectedAgent={selectedAgent}
        setSelectedAgent={setSelectedAgent}
      />
      <main className="flex-1">
        {view === "sessions" && <ChatPanel sessionId={sm.currentId} />}
        {view === "agents" && (
          <AgentEditor
            name={selectedAgent}
            onSelect={setSelectedAgent}
            onDeleted={() => setSelectedAgent(null)}
          />
        )}
        {view === "model" && <ModelConfigPanel />}
      </main>
    </div>
  );
}
