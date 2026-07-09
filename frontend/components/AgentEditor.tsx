"use client";
import { useEffect, useState } from "react";
import {
  AGENT_TOOL_OPTIONS,
  createAgent,
  updateAgent,
  deleteAgent,
  getAgent,
  type AgentDef,
} from "../lib/agents";

type Status = "idle" | "saving" | "error";

// Editor for a single agent definition (.agents/<name>.json).
//   name === null  → new-agent form (name field editable)
//   name === string → edit existing (name field readonly, loaded from /api/agents)
// Save → create (new) or update (existing); Delete → delete + onDeleted().
export function AgentEditor({
  name,
  onSelect,
  onDeleted,
}: {
  name: string | null;
  onSelect: (n: string | null) => void;
  onDeleted: () => void;
}) {
  const existing = name !== null;

  const [nameInput, setNameInput] = useState("");
  const [description, setDescription] = useState("");
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("");
  const [tools, setTools] = useState<string[]>(AGENT_TOOL_OPTIONS.slice(0, 5));
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState<string | null>(null);

  // Load existing agent when `name` changes.
  useEffect(() => {
    setMessage(null);
    setStatus("idle");
    if (name === null) {
      setNameInput("");
      setDescription("");
      setPrompt("");
      setModel("");
      setTools(AGENT_TOOL_OPTIONS.slice(0, 5));
      return;
    }
    let cancelled = false;
    (async () => {
      const a = await getAgent(name);
      if (cancelled) return;
      if (a) {
        setNameInput(a.name);
        setDescription(a.description);
        setPrompt(a.prompt);
        setModel(a.model ?? "");
        setTools(a.tools);
      } else {
        setMessage(`Agent "${name}" not found.`);
        setStatus("error");
      }
    })();
    return () => { cancelled = true; };
  }, [name]);

  async function save() {
    setStatus("saving");
    setMessage(null);
    const body = {
      description,
      prompt,
      model: model.trim() || null,
      tools,
    };
    try {
      if (existing && name) {
        await updateAgent(name, body);
      } else {
        const created = await createAgent({ name: nameInput, ...body });
        onSelect(created.name); // switch to existing-edit mode for the new agent
      }
      setStatus("idle");
      setMessage("已保存。");
    } catch (e) {
      setStatus("error");
      setMessage(`保存失败：${(e as Error).message}`);
    }
  }

  async function remove() {
    if (!existing || !name) return;
    if (!confirm(`Delete agent "${name}"?`)) return;
    setStatus("saving");
    setMessage(null);
    try {
      await deleteAgent(name);
      onDeleted();
    } catch (e) {
      setStatus("error");
      setMessage(`删除失败：${(e as Error).message}`);
    }
  }

  function toggleTool(t: string) {
    setTools((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]));
  }

  return (
    <div className="h-full overflow-y-auto bg-paper-100 px-6 py-5 text-sm text-paper-900">
      <div className="mx-auto max-w-2xl">
        <h2 className="mb-4 text-lg font-semibold text-paper-800">
          {existing ? `编辑智能体` : "新建智能体"}
        </h2>

        <Field label="name">
          <input
            value={nameInput}
            disabled={existing}
            onChange={(e) => setNameInput(e.target.value)}
            placeholder="researcher"
            className={`w-full rounded-md border border-paper-300 bg-paper-50 px-2.5 py-1.5 font-mono text-xs ${
              existing ? "text-paper-400" : "text-paper-900"
            }`}
          />
        </Field>

        <Field label="description">
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="代码库探索子 agent"
            className="w-full rounded-md border border-paper-300 bg-paper-50 px-2.5 py-1.5 text-paper-900"
          />
        </Field>

        <Field label="prompt">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={10}
            placeholder="You are a coding subagent. ... Return only the conclusion."
            className="w-full rounded-md border border-paper-300 bg-paper-50 px-2.5 py-1.5 font-mono text-xs leading-relaxed text-paper-900"
          />
        </Field>

        <Field label="model（留空继承全局）">
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="继承全局"
            className="w-full rounded-md border border-paper-300 bg-paper-50 px-2.5 py-1.5 font-mono text-xs text-paper-900"
          />
        </Field>

        <Field label="tools">
          <div className="flex flex-wrap gap-2">
            {AGENT_TOOL_OPTIONS.map((t) => (
              <label
                key={t}
                className={`flex cursor-pointer items-center gap-1.5 rounded-md border px-2 py-1 font-mono text-xs transition ${
                  tools.includes(t)
                    ? "border-clay-400 bg-clay-50 text-clay-700"
                    : "border-paper-300 bg-paper-50 text-paper-600"
                }`}
              >
                <input
                  type="checkbox"
                  checked={tools.includes(t)}
                  onChange={() => toggleTool(t)}
                  className="accent-clay-500"
                />
                {t}
              </label>
            ))}
          </div>
        </Field>

        <div className="mt-5 flex items-center gap-3">
          <button
            onClick={save}
            disabled={status === "saving" || (!existing && !nameInput.trim())}
            className="rounded-md bg-clay-500 px-4 py-1.5 text-xs font-medium text-white transition hover:bg-clay-600 disabled:opacity-50"
          >
            {status === "saving" ? "保存中…" : "保存"}
          </button>
          {existing && (
            <button
              onClick={remove}
              disabled={status === "saving"}
              className="rounded-md border border-paper-300 px-4 py-1.5 text-xs font-medium text-paper-700 transition hover:bg-paper-200 disabled:opacity-50"
            >
              删除
            </button>
          )}
          {message && (
            <span
              className={`text-xs ${
                status === "error" ? "text-red-500" : "text-paper-500"
              }`}
            >
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
