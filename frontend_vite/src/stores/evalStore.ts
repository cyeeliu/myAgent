import { create } from 'zustand';
import { webRequest } from '../services/webClient';

// ── Types ──

interface DatasetInfo {
  name: string;
  description: string;
  task_count: number;
}

interface RunSummary {
  run_id: string;
  dataset: string;
  model: string;
  status: string;
  started_at: number;
  finished_at: number | null;
}

interface TaskProgress {
  task_id: string;
  rep: number;
  status: string;
  turn?: number;
  tool_call_count?: number;
}

// E-L2: Scorecard values are {mean, stddev, min, max, count} objects,
// not plain numbers. The frontend extracts .mean for display.
interface ScorecardValue {
  mean: number;
  stddev: number;
  min: number;
  max: number;
  count: number;
}

interface Scorecard {
  [metric: string]: ScorecardValue | number | string;
}

// E-M3: per_task entries from the backend are summaries
// {task_id, reps, mean_score, pass_count, status} — they lack
// metrics/judge. The full results array has per-rep detail.
interface TaskResult {
  task_id: string;
  rep?: number;
  reps?: number;
  metrics?: Record<string, number>;
  judge?: Record<string, unknown>;
  mean_score?: number;
  pass_count?: number;
  status: string;
  error?: string;
}

interface EvalReport {
  run_id: string;
  dataset: string;
  model: string;
  scorecard: Scorecard;
  per_task: TaskResult[];
  results?: TaskResult[];  // E-M3: full per-rep results with judge/metrics
  total_tasks: number;
  started_at: number;
  finished_at: number;
}

interface EvalState {
  // Data
  datasets: DatasetInfo[];
  runs: RunSummary[];
  activeRunId: string | null;
  progress: Record<string, TaskProgress>;
  report: EvalReport | null;
  status: 'idle' | 'running' | 'complete' | 'error';
  error: string | null;
  loading: boolean;

  // Config
  selectedDataset: string;
  selectedModel: string;
  selectedMode: string;
  repeat: number;

  // Actions
  loadDatasets: () => Promise<void>;
  loadRuns: () => Promise<void>;
  startRun: () => Promise<void>;
  cancelRun: () => Promise<void>;
  loadRun: (runId: string) => Promise<void>;
  deleteRun: (runId: string) => Promise<void>;
  setSelectedDataset: (d: string) => void;
  setSelectedModel: (m: string) => void;
  setSelectedMode: (m: string) => void;
  setRepeat: (n: number) => void;

  // Event reducers (called by useWebSocket)
  onEvalProgress: (p: { run_id: string; task_id: string; rep: number; status: string; turn?: number; tool_call_count?: number }) => void;
  onEvalTaskComplete: (p: { run_id: string; task_id: string; scores: Record<string, number>; metrics: Record<string, number> }) => void;
  onEvalRunComplete: (p: { run_id: string; scorecard: Scorecard; duration_s: number; total_tasks: number }) => void;
  onEvalRunError: (p: { run_id: string; error: string }) => void;
}

export const useEvalStore = create<EvalState>((set, get) => ({
  datasets: [],
  runs: [],
  activeRunId: null,
  progress: {},
  report: null,
  status: 'idle',
  error: null,
  loading: false,
  selectedDataset: '',
  selectedModel: '',
  selectedMode: 'online',
  repeat: 1,

  loadDatasets: async () => {
    try {
      const data = await webRequest<{ datasets: DatasetInfo[] }>('eval.datasets.list', {});
      set({ datasets: data?.datasets || [] });
    } catch (e) {
      console.error('[evalStore] loadDatasets failed:', e);
    }
  },

  loadRuns: async () => {
    try {
      const data = await webRequest<{ runs: RunSummary[] }>('eval.run.list', { offset: 0, limit: 50 });
      set({ runs: data?.runs || [] });
    } catch (e) {
      console.error('[evalStore] loadRuns failed:', e);
    }
  },

  startRun: async () => {
    const { selectedDataset, selectedModel, selectedMode, repeat } = get();
    if (!selectedDataset || !selectedModel) return;
    set({ status: 'running', error: null, progress: {}, report: null });
    try {
      const data = await webRequest<{ run_id: string }>('eval.run.start', {
        dataset: selectedDataset,
        model: selectedModel,
        mode: selectedMode,
        repeat,
      });
      if (data?.run_id) {
        set({ activeRunId: data.run_id });
      } else {
        set({ status: 'error', error: 'Failed to start run' });
      }
    } catch (err) {
      set({ status: 'error', error: err instanceof Error ? err.message : 'Request failed' });
    }
  },

  cancelRun: async () => {
    const { activeRunId } = get();
    if (!activeRunId) return;
    try {
      await webRequest('eval.run.cancel', { run_id: activeRunId });
      set({ status: 'idle', activeRunId: null });
    } catch {
      // ignore
    }
  },

  loadRun: async (runId: string) => {
    set({ loading: true });
    try {
      const data = await webRequest<EvalReport>('eval.run.get', { run_id: runId });
      set({ report: data, status: 'complete' });
    } catch {
      // ignore
    } finally {
      set({ loading: false });
    }
  },

  deleteRun: async (runId: string) => {
    try {
      await webRequest('eval.run.delete', { run_id: runId });
      set((state) => ({ runs: state.runs.filter((r) => r.run_id !== runId) }));
    } catch {
      // ignore
    }
  },

  setSelectedDataset: (d) => set({ selectedDataset: d }),
  setSelectedModel: (m) => set({ selectedModel: m }),
  setSelectedMode: (m) => set({ selectedMode: m }),
  setRepeat: (n) => set({ repeat: n }),

  // ── Event reducers ──
  onEvalProgress: (p) => {
    if (p.run_id !== get().activeRunId) return;
    set((state) => ({
      progress: { ...state.progress, [p.task_id]: { task_id: p.task_id, rep: p.rep, status: p.status, turn: p.turn, tool_call_count: p.tool_call_count } },
    }));
  },

  onEvalTaskComplete: (_p) => {
    // Task completion — progress already tracked; full results come with run.complete
  },

  onEvalRunComplete: (p) => {
    if (p.run_id !== get().activeRunId) return;
    set({
      status: 'complete',
      report: get().report ? { ...get().report!, scorecard: p.scorecard } : { run_id: p.run_id, dataset: '', model: '', scorecard: p.scorecard, per_task: [], total_tasks: p.total_tasks, started_at: 0, finished_at: 0 },
    });
    // Reload runs list
    void get().loadRuns();
    // Load full report
    void get().loadRun(p.run_id);
  },

  onEvalRunError: (p) => {
    if (p.run_id !== get().activeRunId) return;
    set({ status: 'error', error: p.error });
  },
}));
