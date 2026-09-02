// Gateway global model-config API: GET (masked) / PUT. Mirrors
// agent_gateway.main /api/models routes.
import { GATEWAY } from "./sessions";

export type ModelConfigView = {
  model_id: string;
  base_url: string | null;
  api_key_masked: string | null;
  fallback_model: string | null;
};

export async function getModelConfig(): Promise<ModelConfigView | null> {
  try {
    const r = await fetch(`${GATEWAY}/api/models`);
    if (!r.ok) return null;
    return r.json();
  } catch {
    return null;
  }
}

export async function saveModelConfig(body: {
  model_id: string;
  base_url: string | null;
  api_key: string | null; // empty string = keep existing on-disk key
  fallback_model: string | null;
}): Promise<void> {
  const r = await fetch(`${GATEWAY}/api/models`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
}
