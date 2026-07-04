"use client";
import { ChatPanel } from "../components/ChatPanel";
import { Sidebar } from "../components/Sidebar";
import { useSessionManager } from "../lib/useSessionManager";

// Top-level layout: sidebar (session switcher + live skills/tasks/memory) and
// the chat panel. The session manager (useSessionManager) owns the session
// list + current selection and persists them to localStorage so a page refresh
// reattaches to the same session instead of minting a new one. The ChatPanel
// attaches a transport to whichever session id is current.
export default function Page() {
  const sm = useSessionManager();
  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-100">
      <Sidebar sm={sm} />
      <main className="flex-1">
        <ChatPanel sessionId={sm.currentId} />
      </main>
    </div>
  );
}
