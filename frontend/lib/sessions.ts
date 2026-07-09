// Gateway session API: list / create / delete. The transport (WS/SSE) lives
// in useAgentTransport; this module is just the REST plumbing for the sidebar's
// session manager. Mirrors agent_gateway.main routes.

// Same-origin by default: behind the nginx reverse proxy the gateway API lives
// on the same origin as the page (nginx routes /api/* to the gateway), so we
// can just use window.location.origin and avoid baking an IP/domain at build
// time. Falls back to localhost:8000 for direct `next dev` without nginx.
export const GATEWAY =
  process.env.NEXT_PUBLIC_GATEWAY_URL ||
  (typeof window !== "undefined" ? window.location.origin : "http://localhost:8000");

// Fetch whether a turn is running on the server. Used on (re)connect to seed
// `inFlight`: the replayed event stream has no "turn started" marker, so a
// client reconnecting mid-turn would otherwise leave Send enabled and 409 on
// the next message. Returns null on any failure (treated as "not alive").
export async function sessionWorkerAlive(sid: string): Promise<boolean> {
  try {
    const r = await fetch(`${GATEWAY}/api/sessions/${sid}/status`);
    if (!r.ok) return false;
    return Boolean((await r.json()).worker_alive);
  } catch {
    return false;
  }
}

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
