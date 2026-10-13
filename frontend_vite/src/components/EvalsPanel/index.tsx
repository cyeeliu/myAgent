import { useEffect, useState, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useEvalStore } from '../../stores/evalStore';
import { useObservabilityStore } from '../../stores/observabilityStore';
import { useSessionStore } from '../../stores/sessionStore';
import { webClient } from '../../services/webClient';
import './EvalsPanel.css';

export function EvalsPanel() {
  const { t } = useTranslation();
  const evalStore = useEvalStore();
  const obsStore = useObservabilityStore();
  const { availableModels, selectedModelName } = useSessionStore();
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [traceTaskId, setTraceTaskId] = useState<string | null>(null);
  const [traceData, setTraceData] = useState<string>('');
  const [wsReady, setWsReady] = useState<boolean>(false);
  const [activeTab, setActiveTab] = useState<'eval' | 'obs'>('eval');
  const [trendDataset, setTrendDataset] = useState<string>('');
  const [trendMetric, setTrendMetric] = useState<string>('pass_rate');
  const [compareMode, setCompareMode] = useState<boolean>(false);
  const [compareSelection, setCompareSelection] = useState<Set<string>>(new Set());

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

  // Observability auto-refresh + SSE subscription
  const obsTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (activeTab !== 'obs') return;
    void obsStore.loadAll();
    // SSE subscription for real-time updates when there's an active run
    const activeRun = evalStore.activeRunId;
    if (activeRun && evalStore.status === 'running') {
      obsStore.subscribeSSE(activeRun);
    }
    if (obsStore.autoRefresh) {
      obsTimerRef.current = setInterval(() => void obsStore.loadAll(), obsStore.refreshInterval);
    }
    return () => {
      if (obsTimerRef.current) clearInterval(obsTimerRef.current);
      obsStore.unsubscribeSSE();
    };
  }, [activeTab, obsStore.autoRefresh, obsStore.refreshInterval, evalStore.activeRunId, evalStore.status]); // eslint-disable-line react-hooks/exhaustive-deps

  // Load trend data when dataset/metric changes
  useEffect(() => {
    if (activeTab !== 'obs' || !trendDataset) return;
    void obsStore.loadTrend(trendDataset, trendMetric);
  }, [activeTab, trendDataset, trendMetric]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-set trend dataset from eval store
  useEffect(() => {
    if (!trendDataset && evalStore.selectedDataset) {
      setTrendDataset(evalStore.selectedDataset);
    }
  }, [evalStore.selectedDataset, trendDataset]);

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

        <button
          className={`evals-toolbar__btn ${compareMode ? 'evals-toolbar__btn--cancel' : 'evals-toolbar__btn--ghost'}`}
          onClick={() => { setCompareMode(!compareMode); setCompareSelection(new Set()); }}
        >
          {compareMode ? t('evals.exitCompare', '退出比较') : t('evals.compare', '比较')}
        </button>
        {compareMode && compareSelection.size >= 2 && (
          <button className="evals-toolbar__btn evals-toolbar__btn--run" onClick={() => void evalStore.compareRuns([...compareSelection])}>
            {t('evals.runCompare', '运行比较')} ({compareSelection.size})
          </button>
        )}
      </div>

      {/* Tab Switcher */}
      <div className="evals-tabs">
        <button
          className={`evals-tab ${activeTab === 'eval' ? 'evals-tab--active' : ''}`}
          onClick={() => setActiveTab('eval')}
        >
          {t('evals.evals', '评测')}
        </button>
        <button
          className={`evals-tab ${activeTab === 'obs' ? 'evals-tab--active' : ''}`}
          onClick={() => setActiveTab('obs')}
        >
          {t('evals.observability', '可观测性')}
        </button>
      </div>

      {/* Error */}
      {evalStore.error && (
        <div className="evals-error">{evalStore.error}</div>
      )}
      {activeTab === 'obs' && obsStore.error && (
        <div className="evals-error">{obsStore.error}</div>
      )}

      {/* Observability Tab */}
      {activeTab === 'obs' ? (
        <div className="evals-obs">
          {/* Health Status */}
          <div className="evals-obs__health">
            <span className={`evals-obs__badge evals-obs__badge--${obsStore.health.live === 'ok' ? 'ok' : 'err'}`}>
              {t('evals.obsLive', '存活')}: {obsStore.health.live}
            </span>
            <span className={`evals-obs__badge evals-obs__badge--${obsStore.health.ready === 'ready' ? 'ok' : 'err'}`}>
              {t('evals.obsReady', '就绪')}: {obsStore.health.ready}
            </span>
            {obsStore.sseConnected && (
              <span className="evals-obs__badge evals-obs__badge--ok">SSE</span>
            )}
            <button
              className="evals-toolbar__btn evals-toolbar__btn--ghost"
              style={{ marginLeft: 'auto', padding: '2px 8px', fontSize: 11 }}
              onClick={() => obsStore.setAutoRefresh(!obsStore.autoRefresh)}
            >
              {obsStore.autoRefresh ? '⏸ Auto' : '▶ Auto'}
            </button>
          </div>

          {/* Active Runs Dashboard */}
          <div className="evals-card">
            <div className="evals-card__title">{t('evals.obsDashboard', '实时仪表盘')}</div>
            {obsStore.dashboard?.runs?.length ? (
              <div className="evals-obs__runs">
                {obsStore.dashboard.runs.map((run) => (
                  <div key={run.run_id} className="evals-obs__run">
                    <div className="evals-obs__run-header">
                      <span style={{ fontWeight: 600 }}>{run.dataset}</span>
                      <span style={{ color: 'var(--muted)' }}>{run.model}</span>
                      <span className={`evals-history__status evals-history__status--${run.status}`}>{run.status}</span>
                    </div>
                    <div className="evals-obs__run-progress">
                      <div className="evals-scorecard__bar">
                        <div
                          className="evals-scorecard__bar-fill"
                          style={{ width: `${run.total_tasks > 0 ? (run.completed_tasks / run.total_tasks) * 100 : 0}%` }}
                        />
                      </div>
                      <span style={{ fontSize: 11, color: 'var(--muted)' }}>
                        {run.completed_tasks}/{run.total_tasks}
                        {run.estimated_remaining_ms != null && ` · ETA ${Math.ceil(run.estimated_remaining_ms / 1000)}s`}
                        {run.current_task_id && ` · ${run.current_task_id}`}
                      </span>
                    </div>
                    {run.live_scorecard && Object.keys(run.live_scorecard).length > 0 && (
                      <div className="evals-obs__run-scorecard">
                        {Object.entries(run.live_scorecard).slice(0, 4).map(([k, v]) => (
                          <span key={k} style={{ marginRight: 8, fontSize: 11 }}>
                            {k}={typeof v === 'number' ? v.toFixed(2) : v}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="evals-empty">{t('evals.obsNoActiveRuns', '无活跃运行')}</div>
            )}
          </div>

          {/* Metrics Snapshot */}
          <div className="evals-card">
            <div className="evals-card__title">{t('evals.obsMetrics', '指标快照')}</div>
            {obsStore.metrics && Object.keys(obsStore.metrics).length > 0 ? (
              <div className="evals-obs__metrics">
                {Object.entries(obsStore.metrics).map(([name, info]) => (
                  <div key={name} className="evals-obs__metric">
                    <span className="evals-scorecard__label">{name}</span>
                    <span className="evals-scorecard__value">
                      {typeof info.value === 'number' ? info.value.toFixed(3) : String(info.value)}
                    </span>
                    <span style={{ fontSize: 10, color: 'var(--muted)' }}>{info.direction}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="evals-empty">—</div>
            )}
          </div>

          {/* Alerts */}
          <div className="evals-card">
            <div className="evals-card__title">{t('evals.obsAlerts', '告警')} ({obsStore.alerts.length})</div>
            {obsStore.alerts.length > 0 ? (
              <div className="evals-obs__alerts">
                {obsStore.alerts.map((alert, i) => (
                  <div key={i} className={`evals-obs__alert evals-obs__alert--${alert.severity}`}>
                    <span className={`evals-obs__badge evals-obs__badge--${alert.severity === 'critical' ? 'err' : alert.severity === 'warning' ? 'warn' : 'ok'}`}>
                      {alert.severity}
                    </span>
                    <span style={{ fontWeight: 600 }}>{alert.rule_name}</span>
                    <span style={{ color: 'var(--muted)' }}>
                      {alert.metric}={alert.current_value.toFixed(2)} {alert.state}
                    </span>
                    <span style={{ fontSize: 10, color: 'var(--muted)', marginLeft: 'auto' }}>
                      {new Date(alert.triggered_at * 1000).toLocaleTimeString()}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="evals-empty">{t('evals.obsNoAlerts', '无告警')}</div>
            )}
          </div>

          {/* Trend */}
          <div className="evals-card">
            <div className="evals-card__title">{t('evals.obsTrend', '趋势')}</div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
              <select className="evals-toolbar__select" value={trendDataset} onChange={(e) => setTrendDataset(e.target.value)} style={{ flex: 1 }}>
                {evalStore.datasets.map((d) => (
                  <option key={d.name} value={d.name}>{d.name}</option>
                ))}
              </select>
              <select className="evals-toolbar__select" value={trendMetric} onChange={(e) => setTrendMetric(e.target.value)} style={{ flex: 1 }}>
                <option value="pass_rate">pass_rate</option>
                <option value="tool_success_rate">tool_success_rate</option>
                <option value="mean_turns">mean_turns</option>
                <option value="mean_tool_calls">mean_tool_calls</option>
                <option value="error_rate">error_rate</option>
              </select>
            </div>
            {obsStore.trend.length > 0 ? (
              <div className="evals-obs__trend">
                {obsStore.trend.map((point) => {
                  const val = point[trendMetric] ?? point.value;
                  const change = point.changes?.[trendMetric];
                  return (
                    <div key={point.run_id} className="evals-obs__trend-point">
                      <span style={{ fontSize: 10, color: 'var(--muted)' }}>
                        {point.timestamp > 0 ? new Date(point.timestamp * 1000).toLocaleDateString() : '—'}
                      </span>
                      <span style={{ fontWeight: 600 }}>
                        {typeof val === 'number' ? val.toFixed(3) : '—'}
                      </span>
                      {change && (
                        <span className={`evals-obs__badge evals-obs__badge--${change === 'improved' ? 'ok' : change === 'degraded' ? 'err' : 'warn'}`} style={{ fontSize: 9 }}>
                          {change}
                        </span>
                      )}
                      <span style={{ fontSize: 9, color: 'var(--muted)', marginLeft: 'auto' }}>
                        {point.run_id.slice(-12)}
                      </span>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="evals-empty">{t('evals.obsNoTrend', '无趋势数据')}</div>
            )}
          </div>
        </div>
      ) : (
      /* Body */
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
                    className={`evals-history__item ${compareMode ? (compareSelection.has(run.run_id) ? 'evals-history__item--active' : '') : (selectedRunId === run.run_id ? 'evals-history__item--active' : '')}`}
                    onClick={() => {
                      if (compareMode) {
                        setCompareSelection((prev) => {
                          const next = new Set(prev);
                          if (next.has(run.run_id)) next.delete(run.run_id);
                          else next.add(run.run_id);
                          return next;
                        });
                      } else {
                        void handleSelectRun(run.run_id);
                      }
                    }}
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
          {evalStore.compareResult ? (
            <div className="evals-card">
              <div className="evals-card__title">{t('evals.compareResult', '比较结果')}</div>
              <table className="evals-task-table">
                <thead>
                  <tr>
                    <th>Run</th>
                    {evalStore.compareResult && Object.keys(Object.values(evalStore.compareResult)[0]?.scorecard || {}).slice(0, 6).map((k) => (
                      <th key={k}>{k}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(evalStore.compareResult).map(([rid, data]) => (
                    <tr key={rid}>
                      <td style={{ fontSize: 10 }}>{rid.slice(-20)}</td>
                      {Object.entries(data.scorecard || {}).slice(0, 6).map(([k, v]) => {
                        const numVal = typeof v === 'object' && v !== null && 'mean' in v ? (v as { mean: number }).mean : typeof v === 'number' ? v : parseFloat(String(v));
                        return (
                          <td key={k}>{typeof numVal === 'number' && !isNaN(numVal) ? numVal.toFixed(3) : String(v)}</td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
              <button
                className="evals-toolbar__btn evals-toolbar__btn--ghost"
                style={{ marginTop: 8 }}
                onClick={() => evalStore.clearCompare()}
              >
                {t('common.close', '关闭')}
              </button>
            </div>
          ) : evalStore.loading ? (
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
      )}

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
