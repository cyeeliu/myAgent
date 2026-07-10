// sessionStore — session list + current selection, backed by the method-routed
// /ws RPC (session.list / session.create / session.delete / session.rename).
// Replaces the REST-based lib/sessions.ts for clients on the new architecture;
// the legacy frontend keeps using sessions.ts until migrated.
import { create } from 'zustand';
import { webRequest } from '../services/webClient';
import { ReqMethod } from '../types/websocket';
import type { SessionMeta, SessionStatusInfo } from '../types/message';

const STORAGE_KEY = 'myagent:ws-sessions';

type Persisted = { sessions: SessionMeta[]; currentId: string | null };

function load(): Persisted {
  if (typeof window === 'undefined') return { sessions: [], currentId: null };
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { sessions: [], currentId: null };
    const p = JSON.parse(raw) as Persisted;
    return {
      sessions: Array.isArray(p.sessions) ? p.sessions : [],
      currentId: p.currentId ?? null,
    };
  } catch {
    return { sessions: [], currentId: null };
  }
}

function save(sessions: SessionMeta[], currentId: string | null) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ sessions, currentId }));
  } catch { /* quota */ }
}

interface SessionState {
  sessions: SessionMeta[];
  currentId: string | null;
  loading: boolean;

  setCurrent: (id: string | null) => void;
  refresh: () => Promise<void>;
  create: (transport?: string) => Promise<string | null>;
  remove: (id: string) => Promise<void>;
  rename: (id: string, title: string) => Promise<void>;
  status: (id: string) => Promise<SessionStatusInfo | null>;
  /** Merge a live session meta update (from session.updated events / polling). */
  upsert: (meta: SessionMeta) => void;
}

export const useSessionStore = create<SessionState>((set, get) => ({
  sessions: load().sessions,
  currentId: load().currentId,
  loading: false,

  setCurrent: (id) => {
    set({ currentId: id });
    save(get().sessions, id);
  },

  refresh: async () => {
    set({ loading: true });
    try {
      const res = await webRequest<{ sessions: SessionMeta[] }>(ReqMethod.SESSION_LIST);
      const sessions = res?.sessions ?? [];
      set({ sessions, loading: false });
      const cur = get().currentId;
      if (cur && !sessions.some((m) => m.session_id === cur)) {
        // current session vanished — pick the most recent, or null
        const top = sessions[0];
        set({ currentId: top ? top.session_id : null });
      }
      save(get().sessions, get().currentId);
    } catch {
      set({ loading: false });
    }
  },

  create: async (transport = 'auto') => {
    try {
      const res = await webRequest<{ session_id: string; transport: string }>(
        ReqMethod.SESSION_CREATE, { transport });
      const sid = res.session_id;
      const meta: SessionMeta = {
        session_id: sid,
        transport: res.transport,
        created_at: Date.now() / 1000,
        last_activity: Date.now() / 1000,
        title: '(new session)',
        history_len: 0,
      };
      set((s) => ({
        sessions: [meta, ...s.sessions.filter((m) => m.session_id !== sid)],
        currentId: sid,
      }));
      save(get().sessions, sid);
      return sid;
    } catch {
      return null;
    }
  },

  remove: async (id) => {
    await webRequest(ReqMethod.SESSION_DELETE, { session_id: id }).catch(() => {});
    set((s) => {
      const sessions = s.sessions.filter((m) => m.session_id !== id);
      let currentId = s.currentId;
      if (currentId === id) {
        currentId = sessions[0]?.session_id ?? null;
      }
      save(sessions, currentId);
      return { sessions, currentId };
    });
  },

  rename: async (id, title) => {
    await webRequest(ReqMethod.SESSION_RENAME, { session_id: id, title }).catch(() => {});
    set((s) => ({
      sessions: s.sessions.map((m) =>
        m.session_id === id ? { ...m, title } : m),
    }));
    save(get().sessions, get().currentId);
  },

  status: async (id) => {
    try {
      return await webRequest<SessionStatusInfo>(
        ReqMethod.SESSION_STATUS, { session_id: id });
    } catch {
      return null;
    }
  },

  upsert: (meta) => {
    set((s) => {
      const exists = s.sessions.some((m) => m.session_id === meta.session_id);
      const sessions = exists
        ? s.sessions.map((m) => (m.session_id === meta.session_id ? { ...m, ...meta } : m))
        : [meta, ...s.sessions];
      save(sessions, s.currentId);
      return { sessions };
    });
  },
}));
