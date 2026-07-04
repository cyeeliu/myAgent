"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { listSessions, createSession, deleteSession, type SessionMeta } from "./sessions";

const STORAGE_KEY = "myagent:sessions";

type Persisted = { sessions: SessionMeta[]; currentId: string | null };

function load(): Persisted {
  if (typeof window === "undefined") return { sessions: [], currentId: null };
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { sessions: [], currentId: null };
    const p = JSON.parse(raw) as Persisted;
    return { sessions: Array.isArray(p.sessions) ? p.sessions : [], currentId: p.currentId ?? null };
  } catch {
    return { sessions: [], currentId: null };
  }
}

function save(sessions: SessionMeta[], currentId: string | null) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ sessions, currentId })); } catch { /* quota */ }
}

export interface SessionManager {
  sessions: SessionMeta[];
  currentId: string | null;
  switchTo: (id: string) => void;
  newSession: () => Promise<void>;
  removeSession: (id: string) => Promise<void>;
}

// useSessionManager: owns the session list + current selection, persisted to
// localStorage so a page refresh reattaches to the same session instead of
// minting a new one. On mount it reconciles the cached list with the gateway's
// live list (server restart evicts dead ids), creates an initial session if
// none is live, and polls for title/activity updates.
export function useSessionManager(): SessionManager {
  const [sessions, setSessions] = useState<SessionMeta[]>(() => load().sessions);
  const [currentId, setCurrentId] = useState<string | null>(() => load().currentId);
  const mounted = useRef(false);

  // Reconcile with backend on mount, then poll for updates.
  useEffect(() => {
    mounted.current = true;
    let cancelled = false;

    async function reconcile() {
      let live: SessionMeta[] = [];
      try { live = await listSessions(); } catch { /* gateway down */ }
      if (cancelled) return;

      setSessions((prev) => {
        // Backend is source of truth for which sessions exist; keep our
        // ordering but drop ids the server no longer knows.
        const liveIds = new Set(live.map((m) => m.session_id));
        const kept = prev.filter((m) => liveIds.has(m.session_id));
        const seen = new Set(kept.map((m) => m.session_id));
        const merged = [...kept, ...live.filter((m) => !seen.has(m.session_id))];
        return merged;
      });

      setCurrentId((cur) => {
        if (cur && live.some((m) => m.session_id === cur)) return cur;
        // current session vanished (server restart or deleted) — pick the most
        // recently active live session, or null to trigger creation below.
        if (live.length) {
          const top = [...live].sort((a, b) => b.last_activity - a.last_activity)[0];
          return top.session_id;
        }
        return null;
      });
    }

    (async () => {
      await reconcile();
      if (cancelled) return;
      // If nothing is live at all, create a fresh session.
      setCurrentId((cur) => {
        if (cur || cancelled) return cur;
        createSession().then((sid) => {
          if (cancelled) return;
          setCurrentId(sid);
        }).catch(() => {});
        return cur;
      });
    })();

    const id = setInterval(reconcile, 4000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // Persist whenever state changes.
  useEffect(() => { save(sessions, currentId); }, [sessions, currentId]);

  const switchTo = useCallback((id: string) => {
    setCurrentId(id);
  }, []);

  const newSession = useCallback(async () => {
    const sid = await createSession();
    setSessions((prev) => {
      if (prev.some((m) => m.session_id === sid)) return prev;
      return [
        { session_id: sid, transport: "ws", created_at: Date.now() / 1000,
          last_activity: Date.now() / 1000, title: "(new session)", history_len: 0 },
        ...prev,
      ];
    });
    setCurrentId(sid);
  }, []);

  const removeSession = useCallback(async (id: string) => {
    await deleteSession(id).catch(() => {});
    setSessions((prev) => {
      const next = prev.filter((m) => m.session_id !== id);
      setCurrentId((cur) => {
        if (cur !== id) return cur;
        // deleted the active session — switch to most recent remaining, else create new
        if (next.length) {
          const top = [...next].sort((a, b) => b.last_activity - a.last_activity)[0];
          return top.session_id;
        }
        createSession().then((sid) => { setCurrentId(sid); }).catch(() => {});
        return null;
      });
      return next;
    });
  }, []);

  return { sessions, currentId, switchTo, newSession, removeSession };
}
