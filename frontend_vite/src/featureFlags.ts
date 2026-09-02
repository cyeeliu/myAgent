/**
 * 前端功能开关（集中管理，便于按构建裁剪 UI）
 *
 * myAgent 后端只实现了核心面板（chat / sessions / skills / agents / config /
 * tools）。其余面板（teams / heartbeat / cron / channels / extensions / logs /
 * browser / update）没有后端支撑，在这里关掉以避免死 UI。状态管线（stores 里的
 * team/harness 字段、useWebSocket 里的 team.* 与 harness.* 事件分支）保留不动 ——
 * 它们对空事件 no-op，且 ChatPanel/ToolPanel 的 props 签名依赖它们。
 */
export const FEATURE_APP_UPDATER_UI = false;
export const FEATURE_TEAMS_UI = false;
export const FEATURE_HEARTBEAT_UI = false;
export const FEATURE_CRON_UI = false;
export const FEATURE_CHANNELS_UI = false;
export const FEATURE_EXTENSIONS_UI = false;
export const FEATURE_LOGS_UI = false;
export const FEATURE_BROWSER_UI = false;
export const FEATURE_EVALS_UI = true;
