"use client";
import { useEffect, useState } from "react";
import { getModelConfig, saveModelConfig, type ModelConfigView } from "../lib/models";

type Status = "idle" | "saving" | "error";

// Global model config editor (.agents/model.json). api_key is displayed masked;
// a separate empty input lets the user enter a new key to replace it (empty on
// Save = keep the existing on-disk key). Save → PUT /api/models; the new config
// takes effect next turn (loop re-reads model_config each round).
export function ModelConfigPanel() {
  const [cfg, setCfg] = useState<ModelConfigView | null>(null);
  const [modelId, setModelId] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [fallback, setFallback] = useState("");
  const [newKey, setNewKey] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const c = await getModelConfig();
      if (cancelled || !c) return;
      setCfg(c);
      setModelId(c.model_id);
      setBaseUrl(c.base_url ?? "");
      setFallback(c.fallback_model ?? "");
    })();
    return () => { cancelled = true; };
  }, []);

  async function save() {
    setStatus("saving");
    setMessage(null);
    try {
      await saveModelConfig({
        model_id: modelId,
        base_url: baseUrl.trim() || null,
        api_key: newKey.trim() || null, // empty → keep existing
        fallback_model: fallback.trim() || null,
      });
      setNewKey("");
      setStatus("idle");
      setMessage("已保存。下一轮生效。");
      // refresh masked view
      const c = await getModelConfig();
      if (c) setCfg(c);
    } catch (e) {
      setStatus("error");
      setMessage(`保存失败：${(e as Error).message}`);
    }
  }

  if (cfg === null) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-paper-500">
        加载中…
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto bg-paper-100 px-6 py-5 text-sm text-paper-900">
      <div className="mx-auto max-w-2xl">
        <h2 className="mb-1 text-lg font-semibold text-paper-800">模型配置</h2>
        <p className="mb-4 text-xs text-paper-500">
          全局单例。修改后下一轮生效（在跑会话的当前轮不受影响）。
        </p>

        <Field label="model_id">
          <input
            value={modelId}
            onChange={(e) => setModelId(e.target.value)}
            className="w-full rounded-md border border-paper-300 bg-paper-50 px-2.5 py-1.5 font-mono text-xs text-paper-900"
          />
        </Field>

        <Field label="base_url">
          <input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://api.z.ai/api/openai"
            className="w-full rounded-md border border-paper-300 bg-paper-50 px-2.5 py-1.5 font-mono text-xs text-paper-900"
          />
        </Field>

        <Field label="api_key">
          <div className="space-y-1.5">
            <div className="w-full rounded-md border border-paper-300 bg-paper-50 px-2.5 py-1.5 font-mono text-xs text-paper-400">
              {cfg.api_key_masked ?? "(未设置)"}
            </div>
            <input
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              placeholder="输入新 key 以更改（留空保持不变）"
              type="password"
              className="w-full rounded-md border border-paper-300 bg-paper-50 px-2.5 py-1.5 font-mono text-xs text-paper-900"
            />
          </div>
        </Field>

        <Field label="fallback_model（留空表示无 fallback）">
          <input
            value={fallback}
            onChange={(e) => setFallback(e.target.value)}
            placeholder="glm-4"
            className="w-full rounded-md border border-paper-300 bg-paper-50 px-2.5 py-1.5 font-mono text-xs text-paper-900"
          />
        </Field>

        <div className="mt-5 flex items-center gap-3">
          <button
            onClick={save}
            disabled={status === "saving" || !modelId.trim()}
            className="rounded-md bg-clay-500 px-4 py-1.5 text-xs font-medium text-white transition hover:bg-clay-600 disabled:opacity-50"
          >
            {status === "saving" ? "保存中…" : "保存"}
          </button>
          {message && (
            <span className={`text-xs ${status === "error" ? "text-red-500" : "text-paper-500"}`}>
              {message}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-3.5">
      <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-paper-500">
        {label}
      </label>
      {children}
    </div>
  );
}
