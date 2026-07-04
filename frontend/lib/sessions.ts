// Gateway session API: list / create / delete. The transport (WS/SSE) lives
// in useAgentTransport; this module is just the REST plumbing for the sidebar's
// session manager. Mirrors agent_gateway.main routes.

const GATEWAY = process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8000";

export type SessionMeta = {
  session_id: string;
  transport: string;
  created_at: number;
  last_activity: number;
  title: string;
  history_len: number;
};

export async function listSessions(): Promise<SessionMeta[]> {
  const r = await fetch(`${GATEWAY}/api/sessions`);
  if (!r.ok) return [];
  const a = await r.json();
  return Array.isArray(a) ? a : [];
}

export async function createSession(): Promise<string> {
  const r = await fetch(`${GATEWAY}/api/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transport: "auto" }),
  });
  const { session_id } = await r.json();
  return session_id as string;
}

export async function deleteSession(sid: string): Promise<void> {
  await fetch(`${GATEWAY}/api/sessions/${sid}`, { method: "DELETE" });
}
