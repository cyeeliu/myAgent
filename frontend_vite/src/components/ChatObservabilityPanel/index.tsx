import { useChatObservabilityStore } from '../../stores/chatObservabilityStore';
import { useTranslation } from 'react-i18next';

export function ChatObservabilityPanel() {
  const { t } = useTranslation();
  const obs = useChatObservabilityStore();

  const recentSpans = obs.spanEvents.slice(-30).reverse();
  const ctxRate = obs.contextUsage?.rate ?? 0;

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 12,
      padding: 12, overflowY: 'auto', height: '100%',
      fontSize: 12, color: 'var(--text, #333)',
    }}>
      {/* Token Usage */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <div style={cardStyle}>
          <div style={labelStyle}>{t('chatObs.tokensIn', '输入 Token')}</div>
          <div style={valueStyle}>{obs.totalTokensIn.toLocaleString()}</div>
        </div>
        <div style={cardStyle}>
          <div style={labelStyle}>{t('chatObs.tokensOut', '输出 Token')}</div>
          <div style={valueStyle}>{obs.totalTokensOut.toLocaleString()}</div>
        </div>
        <div style={cardStyle}>
          <div style={labelStyle}>{t('chatObs.totalTokens', '总 Token')}</div>
          <div style={valueStyle}>{(obs.totalTokensIn + obs.totalTokensOut).toLocaleString()}</div>
        </div>
        <div style={cardStyle}>
          <div style={labelStyle}>{t('chatObs.turns', '轮次')}</div>
          <div style={valueStyle}>{obs.turnCount}</div>
        </div>
        <div style={cardStyle}>
          <div style={labelStyle}>{t('chatObs.toolCalls', '工具调用')}</div>
          <div style={valueStyle}>{obs.toolCallCount}</div>
        </div>
      </div>

      {/* Context Window Usage */}
      {obs.contextUsage && (
        <div style={{ ...cardStyle, padding: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
            <span style={labelStyle}>{t('chatObs.contextWindow', '上下文窗口')}</span>
            <span style={{ color: ctxRate > 80 ? '#ef4444' : ctxRate > 60 ? '#f59e0b' : 'var(--muted)' }}>
              {obs.contextUsage.tokens_used.toLocaleString()} / {obs.contextUsage.context_max.toLocaleString()} ({ctxRate.toFixed(1)}%)
            </span>
          </div>
          <div style={{
            height: 6, borderRadius: 3, background: 'var(--border, #e0e0e0)', overflow: 'hidden',
          }}>
            <div style={{
              height: '100%', borderRadius: 3,
              width: `${Math.min(100, ctxRate)}%`,
              background: ctxRate > 80 ? '#ef4444' : ctxRate > 60 ? '#f59e0b' : '#22c55e',
              transition: 'width 0.3s',
            }} />
          </div>
        </div>
      )}

      {/* Span Timeline */}
      {recentSpans.length > 0 && (
        <div style={{ ...cardStyle, padding: 8 }}>
          <div style={{ ...labelStyle, marginBottom: 6 }}>{t('chatObs.timeline', '执行时间线')}</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {recentSpans.map((span, i) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', gap: 6, padding: '2px 4px',
                borderRadius: 3,
                background: span.name === 'llm_call' ? '#f0f7ff' : span.name === 'tool_call' ? '#f0fff4' : 'transparent',
              }}>
                <span style={{ fontSize: 10, color: 'var(--muted)', minWidth: 48 }}>
                  {new Date(span.timestamp).toLocaleTimeString([], { hour12: false })}
                </span>
                <span style={{
                  fontWeight: 600, minWidth: 60,
                  color: span.name === 'llm_call' ? '#2563eb' : '#22c55e',
                }}>
                  {span.name === 'llm_call' ? 'LLM' : span.tool || span.name}
                </span>
                {span.duration_ms != null && (
                  <span style={{ color: 'var(--muted)', minWidth: 50 }}>
                    {span.duration_ms < 1000
                      ? `${Math.round(span.duration_ms)}ms`
                      : `${(span.duration_ms / 1000).toFixed(1)}s`}
                  </span>
                )}
                {span.tokens_in != null && span.tokens_in > 0 && (
                  <span style={{ color: '#2563eb', fontSize: 10 }}>↓{span.tokens_in}</span>
                )}
                {span.tokens_out != null && span.tokens_out > 0 && (
                  <span style={{ color: '#22c55e', fontSize: 10 }}>↑{span.tokens_out}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Compaction Events */}
      {obs.compactionEvents.length > 0 && (
        <div style={{ ...cardStyle, padding: 8 }}>
          <div style={{ ...labelStyle, marginBottom: 6 }}>
            {t('chatObs.compactions', '上下文压缩')} ({obs.compactionEvents.length})
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {obs.compactionEvents.slice(-5).reverse().map((c, i) => (
              <div key={i} style={{ display: 'flex', gap: 6, fontSize: 11 }}>
                <span style={{ color: 'var(--muted)' }}>
                  {new Date(c.timestamp).toLocaleTimeString([], { hour12: false })}
                </span>
                <span style={{
                  padding: '0 4px', borderRadius: 3, fontSize: 10,
                  background: c.reason === 'reactive_compact' ? '#fef2f2' : '#f0f7ff',
                  color: c.reason === 'reactive_compact' ? '#991b1b' : '#2563eb',
                }}>
                  {c.reason}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Memory Events */}
      {obs.memoryEvents.length > 0 && (
        <div style={{ ...cardStyle, padding: 8 }}>
          <div style={{ ...labelStyle, marginBottom: 6 }}>
            {t('chatObs.memory', '记忆提取')} ({obs.memoryEvents.length})
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {obs.memoryEvents.slice(-5).reverse().map((m, i) => (
              <div key={i} style={{ display: 'flex', gap: 6, fontSize: 11 }}>
                <span style={{ color: 'var(--muted)' }}>
                  {new Date(m.timestamp).toLocaleTimeString([], { hour12: false })}
                </span>
                <span style={{ color: 'var(--muted)' }}>{m.extracted}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Reset button */}
      <button
        onClick={() => obs.reset()}
        style={{
          padding: '4px 8px', fontSize: 11, cursor: 'pointer',
          border: '1px solid var(--border, #e0e0e0)', borderRadius: 4,
          background: 'none', color: 'var(--muted)', alignSelf: 'flex-start',
        }}
      >
        {t('chatObs.reset', '重置统计')}
      </button>
    </div>
  );
}

const cardStyle: React.CSSProperties = {
  border: '1px solid var(--border, #e0e0e0)',
  borderRadius: 6,
  padding: 8,
  flex: '1 1 auto',
  minWidth: 0,
};

const labelStyle: React.CSSProperties = {
  fontSize: 10,
  color: 'var(--muted)',
  fontWeight: 500,
};

const valueStyle: React.CSSProperties = {
  fontSize: 16,
  fontWeight: 600,
};
