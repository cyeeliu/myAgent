import { create } from 'zustand';

// ── Types ──

interface DashboardRun {
  run_id: string;
  dataset: string;
  model: string;
  mode: string;
  started_at: number;
  status: string;
  completed_tasks: number;
  total_tasks: number;
  current_task_id: string;
  elapsed_ms: number;
  estimated_remaining_ms: number | null;
  live_scorecard: Record<string, number>;
  task_details: Array<{ task_id: string; status: string; duration_ms: number; rep: number }>;
}

interface MetricSnapshot {
  [name: string]: {
    value: number | string;
    description: string;
    direction: string;
    run_id?: string;
  };
}

interface AlertEvent {
  rule_name: string;
  run_id: string;
  metric: string;
  current_value: number;
  threshold: number;
  severity: string;
  triggered_at: number;
  state: string;
}

interface TrendPoint {
  run_id: string;
  timestamp: number;
  [metric: string]: number | string;
  changes: Record<string, string>;
}

interface HealthStatus {
  live: 'ok' | 'unknown';
  ready: 'ready' | 'not ready' | 'unknown';
}

// ── Store ──

interface ObservabilityState {
  dashboard: { runs: DashboardRun[] } | null;
  metrics: MetricSnapshot | null;
  alerts: AlertEvent[];
  trend: TrendPoint[];
  health: HealthStatus;
  loading: boolean;
  error: string | null;
  autoRefresh: boolean;
  refreshInterval: number;

  loadDashboard: () => Promise<void>;
  loadMetrics: (runId?: string) => Promise<void>;
  loadAlerts: () => Promise<void>;
  loadTrend: (dataset: string, metrics: string) => Promise<void>;
  loadHealth: () => Promise<void>;
  loadAll: () => Promise<void>;
  setAutoRefresh: (enabled: boolean) => void;
  clearError: () => void;
}

const API_BASE = '/eval/obs';

async function fetchJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export const useObservabilityStore = create<ObservabilityState>((set, get) => ({
  dashboard: null,
  metrics: null,
  alerts: [],
  trend: [],
  health: { live: 'unknown', ready: 'unknown' },
  loading: false,
  error: null,
  autoRefresh: true,
  refreshInterval: 3000,

  loadDashboard: async () => {
    try {
      const data = await fetchJSON<{ runs: DashboardRun[] }>(`${API_BASE}/dashboard`);
      set({ dashboard: data });
    } catch (e) {
      set({ error: String(e) });
    }
  },

  loadMetrics: async (runId?: string) => {
    try {
      const url = runId
        ? `${API_BASE}/metrics?run_id=${encodeURIComponent(runId)}&format=json`
        : `${API_BASE}/metrics?format=json`;
      const data = await fetchJSON<MetricSnapshot>(url);
      set({ metrics: data });
    } catch (e) {
      set({ error: String(e) });
    }
  },

  loadAlerts: async () => {
    try {
      const data = await fetchJSON<AlertEvent[]>(`${API_BASE}/alerts?limit=50`);
      set({ alerts: data });
    } catch (e) {
      set({ error: String(e) });
    }
  },

  loadTrend: async (dataset: string, metrics: string) => {
    try {
      const url = `${API_BASE}/trend?dataset=${encodeURIComponent(dataset)}&metrics=${encodeURIComponent(metrics)}&limit=20`;
      const data = await fetchJSON<TrendPoint[]>(url);
      set({ trend: data });
    } catch (e) {
      set({ error: String(e) });
    }
  },

  loadHealth: async () => {
    try {
      const liveRes = await fetch(`${API_BASE}/health/live`);
      set((s) => ({ health: { ...s.health, live: liveRes.ok ? 'ok' : 'unknown' } }));
    } catch { /* ignore */ }
    try {
      const readyRes = await fetch(`${API_BASE}/health/ready`);
      const readyData = readyRes.ok ? await readyRes.json() : null;
      set((s) => ({
        health: {
          ...s.health,
          ready: readyData?.status === 'ready' ? 'ready' : 'not ready',
        },
      }));
    } catch {
      set((s) => ({ health: { ...s.health, ready: 'not ready' } }));
    }
  },

  loadAll: async () => {
    set({ loading: true, error: null });
    try {
      await Promise.all([
        get().loadDashboard(),
        get().loadMetrics(),
        get().loadAlerts(),
        get().loadHealth(),
      ]);
    } finally {
      set({ loading: false });
    }
  },

  setAutoRefresh: (enabled: boolean) => set({ autoRefresh: enabled }),
  clearError: () => set({ error: null }),
}));
