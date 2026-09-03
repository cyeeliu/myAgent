import { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useEvalStore } from '../../stores/evalStore';
import { useSessionStore } from '../../stores/sessionStore';
import { webClient } from '../../services/webClient';
import './EvalsPanel.css';

export function EvalsPanel() {
  const { t } = useTranslation();
  const evalStore = useEvalStore();
  const { availableModels, selectedModelName } = useSessionStore();
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [traceTaskId, setTraceTaskId] = useState<string | null>(null);
  const [traceData, setTraceData] = useState<string>('');
  const [wsReady, setWsReady] = useState<boolean>(false);

  // Subscribe to WS connection state — retry loadDatasets/loadRuns when ready
  useEffect(() => {
    const unsub = webClient.onStateChange((state) => {
      setWsReady(state === 'ready');
    });
    // Also check current state immediately
    setWsReady(webClient.getState() === 'ready');
    return () => { unsub(); };
  }, []);

  // Load datasets and runs on mount AND when WS becomes ready
  useEffect(() => {
    if (!wsReady) return;
    void evalStore.loadDatasets();
    void evalStore.loadRuns();
  }, [wsReady]); // eslint-disable-line react-hooks/exhaustive-deps

  // Set default model
  useEffect(() => {
    if (!evalStore.selectedModel && selectedModelName) {
      evalStore.setSelectedModel(selectedModelName);
    }
  }, [selectedModelName, evalStore.selectedModel]); // eslint-disable-line react-hooks/exhaustive-deps

  // Set default dataset
  useEffect(() => {
    if (!evalStore.selectedDataset && evalStore.datasets.length > 0) {
      evalStore.setSelectedDataset(evalStore.datasets[0].name);
    }
  }, [evalStore.datasets, evalStore.selectedDataset]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSelectRun = useCallback(async (runId: string) => {
    setSelectedRunId(runId);
    await evalStore.loadRun(runId);
  }, [evalStore]);

  const handleDeleteRun = useCallback(async (e: React.MouseEvent, runId: string) => {
    e.stopPropagation();
    await evalStore.deleteRun(runId);
    if (selectedRunId === runId) setSelectedRunId(null);
  }, [evalStore, selectedRunId]);

  const isRunning = evalStore.status === 'running';
  const report = evalStore.report;

  // E-H3: Fetch the actual trace from the REST API when a task is clicked.
  useEffect(() => {
    if (!traceTaskId || !selectedRunId) {
      setTraceData('');
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/eval/runs/${encodeURIComponent(selectedRunId)}/results/${encodeURIComponent(traceTaskId)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) setTraceData(JSON.stringify(data, null, 2));
      } catch {
        if (!cancelled) {
          // Fallback: show what we have from the report
          const task = report?.results?.find((r) => r.task_id === traceTaskId)
            || report?.per_task?.find((r) => r.task_id === traceTaskId);
          setTraceData(task ? JSON.stringify(task, null, 2) : 'Not found');
        }
      }
    })();
    return () => { cancelled = true; };
  }, [traceTaskId, selectedRunId, report]);

  return (
    <div className="evals-panel">
      {/* Toolbar */}
      <div className="evals-toolbar">
        <span className="evals-toolbar__label">{t('evals.dataset')}:</span>
        <select
          className="evals-toolbar__select"
          value={evalStore.selectedDataset}
          onChange={(e) => evalStore.setSelectedDataset(e.target.value)}
          disabled={isRunning}
        >
          {!wsReady && <option value="" disabled>{t('common.loading')}</option>}
          {wsReady && evalStore.datasets.length === 0 && <option value="" disabled>—</option>}
          {evalStore.datasets.map((d) => (
            <option key={d.name} value={d.name}>
              {d.name} ({d.task_count} tasks)
            </option>
          ))}
        </select>

        <span className="evals-toolbar__label">{t('evals.model')}:</span>
        <select
          className="evals-toolbar__select"
          value={evalStore.selectedModel}
          onChange={(e) => evalStore.setSelectedModel(e.target.value)}
          disabled={isRunning}
        >
          {availableModels.map((m) => (
            <option key={m.model_name} value={m.model_name}>{m.model_name}</option>
          ))}
        </select>

        <span className="evals-toolbar__label">{t('evals.mode')}:</span>
        <select
          className="evals-toolbar__select"
          value={evalStore.selectedMode}
          onChange={(e) => evalStore.setSelectedMode(e.target.value)}
          disabled={isRunning}
        >
          <option value="online">{t('evals.modeOnline')}</option>
          <option value="mock">{t('evals.modeMock')}</option>
        </select>

        <span className="evals-toolbar__label">{t('evals.repeat')}:</span>
        <input
          className="evals-toolbar__input"
          type="number"
          min={1}
          max={5}
          value={evalStore.repeat}
          onChange={(e) => evalStore.setRepeat(Math.max(1, Math.min(5, parseInt(e.target.value) || 1)))}
          disabled={isRunning}
          style={{ width: 50 }}
        />

        {isRunning ? (
          <button className="evals-toolbar__btn evals-toolbar__btn--cancel" onClick={() => void evalStore.cancelRun()}>
            {t('evals.cancel')}
          </button>
        ) : (
          <button
            className="evals-toolbar__btn evals-toolbar__btn--run"
            onClick={() => void evalStore.startRun()}
            disabled={!evalStore.selectedDataset || !evalStore.selectedModel}
          >
            {t('evals.startRun')}
          </button>
        )}

        <button className="evals-toolbar__btn evals-toolbar__btn--ghost" onClick={() => void evalStore.loadRuns()}>
          {t('common.refresh')}
        </button>
      </div>

      {/* Error */}
      {evalStore.error && (
        <div className="evals-error">{evalStore.error}</div>
      )}

      {/* Body */}
      <div className="evals-body">
        {/* Left: Run History */}
        <div className="evals-left">
          <div className="evals-card">
            <div className="evals-card__title">{t('evals.runHistory')}</div>
            {evalStore.runs.length === 0 ? (
              <div className="evals-empty">{t('evals.noRuns')}</div>
            ) : (
              <div className="evals-history">
                {evalStore.runs.map((run) => (
                  <div
                    key={run.run_id}
                    className={`evals-history__item ${selectedRunId === run.run_id ? 'evals-history__item--active' : ''}`}
                    onClick={() => void handleSelectRun(run.run_id)}
                  >
                    <div className="evals-history__meta">
                      <span className="evals-history__id">{run.dataset}</span>
                      <span className="evals-history__sub">{run.model} · {run.started_at > 0 ? new Date(run.started_at * 1000).toLocaleString() : '—'}</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span className={`evals-history__status evals-history__status--${run.status}`}>{run.status}</span>
                      <button
                        className="evals-toolbar__btn evals-toolbar__btn--ghost"
                        style={{ padding: '2px 6px', fontSize: 10 }}
                        onClick={(e) => void handleDeleteRun(e, run.run_id)}
                      >
                        ✕
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Progress (when running) */}
          {isRunning && Object.keys(evalStore.progress).length > 0 && (
            <div className="evals-card">
              <div className="evals-card__title">{t('evals.progress')}</div>
              <div className="evals-progress">
                {Object.values(evalStore.progress).map((p) => (
                  <div key={p.task_id} className={`evals-progress__item evals-progress__item--${p.status === 'ok' ? 'done' : p.status === 'error' ? 'error' : 'running'}`}>
                    <span className="evals-progress__icon">
                      {p.status === 'ok' ? '✓' : p.status === 'error' ? '✗' : '⋯'}
                    </span>
                    <span>{p.task_id}</span>
                    {p.turn !== undefined && <span style={{ color: 'var(--muted)', marginLeft: 'auto' }}>turn {p.turn}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Right: Scorecard + Task Results */}
        <div className="evals-right">
          {evalStore.loading ? (
            <div className="evals-empty">{t('common.loading')}</div>
          ) : report ? (
            <>
              {/* Scorecard */}
              {report.scorecard && Object.keys(report.scorecard).length > 0 && (
                <div className="evals-card">
                  <div className="evals-card__title">{t('evals.scorecard')}</div>
                  <div className="evals-scorecard">
                    {Object.entries(report.scorecard).map(([key, value]) => {
                      // E-C2: scorecard values are {mean, stddev, min, max, count}
                      // objects — extract .mean for display. Fall back to plain
                      // number/string for backward compat.
                      const numVal = typeof value === 'number'
                        ? value
                        : typeof value === 'object' && value !== null && 'mean' in value
                          ? (value as { mean: number }).mean
                          : parseFloat(String(value));
                      const pct = isNaN(numVal) ? 0 : Math.max(0, Math.min(100, numVal * 100));
                      return (
                        <div key={key} className="evals-scorecard__item">
                          <span className="evals-scorecard__label">{key}</span>
                          <span className="evals-scorecard__value">{typeof numVal === 'number' && !isNaN(numVal) ? numVal.toFixed(3) : String(value)}</span>
                          <div className="evals-scorecard__bar">
                            <div className="evals-scorecard__bar-fill" style={{ width: `${pct}%` }} />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Task Results Table */}
              {/* E-M3: Use report.results (full per-rep with judge/metrics) when
                  available; fall back to per_task summaries. */}
              {(() => {
                const taskRows = report.results || report.per_task || [];
                return taskRows.length > 0 ? (
                <div className="evals-card">
                  <div className="evals-card__title">{t('evals.taskResults')}</div>
                  <table className="evals-task-table">
                    <thead>
                      <tr>
                        <th>{t('evals.taskId')}</th>
                        <th>{t('evals.status')}</th>
                        <th>{t('evals.metrics')}</th>
                        <th>{t('evals.judge')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {taskRows.map((task, i) => (
                        <tr key={`${task.task_id}-${i}`} onClick={() => setTraceTaskId(task.task_id)}>
                          <td>{task.task_id}</td>
                          <td>
                            <span className="evals-task-table__status">
                              {task.status === 'ok' ? '✓' : task.status === 'error' ? '✗' : '⋯'}
                            </span>
                            {task.status}
                          </td>
                          <td>
                            {/* E-M3: results entries have judge but no metrics;
                                per_task summaries have mean_score/pass_count. */}
                            {task.metrics && Object.keys(task.metrics).length > 0
                              ? Object.entries(task.metrics).slice(0, 3).map(([k, v]) => (
                                  <span key={k} style={{ marginRight: 6 }}>{k}={typeof v === 'number' ? v.toFixed(2) : v}</span>
                                ))
                              : task.mean_score !== undefined
                                ? <span style={{ color: 'var(--muted)' }}>score={task.mean_score.toFixed(2)}</span>
                                : '-'}
                          </td>
                          <td>
                            {task.judge && typeof task.judge === 'object' && 'score' in task.judge
                              ? String(task.judge.score)
                              : task.pass_count !== undefined
                                ? `${task.pass_count}/${task.reps || '?'} passed`
                                : '-'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                ) : null;
              })()}
            </>
          ) : (
            <div className="evals-empty">{t('evals.selectRunPrompt')}</div>
          )}
        </div>
      </div>

      {/* Trace Drawer */}
      {traceTaskId && report && (
        <div className="evals-drawer-overlay" onClick={() => setTraceTaskId(null)}>
          <div className="evals-drawer" onClick={(e) => e.stopPropagation()}>
            <div className="evals-drawer__header">
              <span style={{ fontWeight: 600 }}>{t('evals.taskTrace')}: {traceTaskId}</span>
              <button className="evals-toolbar__btn evals-toolbar__btn--ghost" onClick={() => setTraceTaskId(null)}>
                ✕
              </button>
            </div>
            <div className="evals-drawer__body">
              {traceData || 'Loading...'}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
