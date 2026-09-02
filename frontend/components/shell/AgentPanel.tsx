"use client";
// Agent panel — agents.list / agents.create / agents.delete via the method-routed WS.
import { useEffect, useState } from "react";
import { webRequest } from "../../lib/services/webClient";
import { ReqMethod } from "../../lib/types/websocket";
import type { AgentEntry } from "../../lib/types/message";

export function AgentPanel() {
  const [agents, setAgents] = useState<AgentEntry[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [prompt, setPrompt] = useState("");

  const load = () => {
    webRequest<{ agents: AgentEntry[] }>(ReqMethod.AGENTS_LIST)
      .then((r) => setAgents(r.agents || []))
      .catch(() => {});
  };
  useEffect(load, []);

  const create = async () => {
    if (!name.trim()) return;
    await webRequest(ReqMethod.AGENTS_CREATE, {
      name, description, prompt, tools: [],
    });
    setName(""); setDescription(""); setPrompt("");
    load();
  };

  const remove = async (n: string) => {
    await webRequest(ReqMethod.AGENTS_DELETE, { name: n });
    load();
  };

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <h2 className="mb-6 text-lg font-semibold text-paper-900">智能体</h2>
      <div className="mb-6 space-y-3 rounded-xl border border-paper-300 bg-paper-50 p-5">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="名称"
          className="w-full rounded-lg border border-paper-300 px-3 py-2 text-sm" />
        <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="描述"
          className="w-full rounded-lg border border-paper-300 px-3 py-2 text-sm" />
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="系统提示词" rows={4}
          className="w-full rounded-lg border border-paper-300 px-3 py-2 text-sm" />
        <button onClick={create}
          className="rounded-lg bg-clay-500 px-4 py-2 text-sm text-white hover:bg-clay-600">
          新建
        </button>
      </div>
      <ul className="divide-y divide-paper-200 rounded-xl border border-paper-300 bg-paper-50">
        {agents.map((a) => (
          <li key={a.name} className="flex items-center justify-between px-4 py-3">
            <div>
              <div className="text-sm font-medium text-paper-900">{a.name}</div>
              {a.description && <div className="text-xs text-paper-600">{a.description}</div>}
            </div>
            <button onClick={() => remove(a.name)}
              className="text-xs text-paper-500 hover:text-red-500">删除</button>
          </li>
        ))}
      </ul>
    </div>
  );
}
