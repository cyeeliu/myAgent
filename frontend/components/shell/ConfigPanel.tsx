"use client";
// Config panel — models.list / config.get / config.set via the method-routed WS.
import { useEffect, useState } from "react";
import { webRequest } from "../../lib/services/webClient";
import { ReqMethod } from "../../lib/types/websocket";
import type { ConfigInfo, ModelEntry } from "../../lib/types/message";

export function ConfigPanel() {
  const [config, setConfig] = useState<ConfigInfo | null>(null);
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [active, setActive] = useState("");
  const [modelId, setModelId] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const cfg = await webRequest<ConfigInfo>(ReqMethod.CONFIG_GET);
      setConfig(cfg);
      setModelId(cfg.model_id);
      setBaseUrl(cfg.base_url || "");
    } catch { /* ignore */ }
    try {
      const res = await webRequest<{ models: ModelEntry[]; active_model: string }>(
        ReqMethod.MODELS_LIST);
      setModels(res.models || []);
      setActive(res.active_model || "");
    } catch { /* ignore */ }
  };
  useEffect(() => { void load(); }, []);

  const save = async () => {
    setSaving(true);
    try {
      await webRequest(ReqMethod.CONFIG_SET, {
        model_id: modelId, base_url: baseUrl, api_key: apiKey || undefined,
      });
      await load();
    } finally { setSaving(false); }
  };

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <h2 className="mb-6 text-lg font-semibold text-paper-900">模型配置</h2>
      <div className="space-y-4 rounded-xl border border-paper-300 bg-paper-50 p-5">
        <label className="block">
          <span className="text-xs font-medium text-paper-700">模型 ID</span>
          <input value={modelId} onChange={(e) => setModelId(e.target.value)}
            className="mt-1 w-full rounded-lg border border-paper-300 px-3 py-2 text-sm" />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-paper-700">Base URL</span>
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
            className="mt-1 w-full rounded-lg border border-paper-300 px-3 py-2 text-sm" />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-paper-700">API Key（留空保留原值）</span>
          <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
            placeholder={config?.api_key_masked || "未设置"}
            className="mt-1 w-full rounded-lg border border-paper-300 px-3 py-2 text-sm" />
        </label>
        <button onClick={save} disabled={saving}
          className="rounded-lg bg-clay-500 px-4 py-2 text-sm text-white hover:bg-clay-600 disabled:opacity-50">
          {saving ? "保存中…" : "保存"}
        </button>
      </div>
      {models.length > 0 && (
        <div className="mt-6">
          <h3 className="mb-2 text-sm font-medium text-paper-700">可用模型</h3>
          <ul className="divide-y divide-paper-200 rounded-xl border border-paper-300 bg-paper-50 text-sm">
            {models.map((m) => (
              <li key={m.model_name} className="flex items-center justify-between px-4 py-2.5">
                <span className="text-paper-900">{m.model_name}</span>
                {m.model_name === active && (
                  <span className="text-xs text-clay-600">当前</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
