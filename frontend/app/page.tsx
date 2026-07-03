"use client";
import { ChatPanel } from "../components/ChatPanel";
import { Sidebar } from "../components/Sidebar";

// Top-level layout: sidebar (live skills/tasks/memory) + chat panel (the
// transport-driven conversation). The ChatPanel owns the session and the
// transport via useAgentTransport (spec §6).
export default function Page() {
  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-100">
      <Sidebar />
      <main className="flex-1">
        <ChatPanel />
      </main>
    </div>
  );
}
