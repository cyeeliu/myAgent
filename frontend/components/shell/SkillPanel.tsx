"use client";
// Skill panel — skills.list via the method-routed WS.
import { useEffect, useState } from "react";
import { webRequest } from "../../lib/services/webClient";
import { ReqMethod } from "../../lib/types/websocket";
import type { SkillEntry } from "../../lib/types/message";

export function SkillPanel() {
  const [skills, setSkills] = useState<SkillEntry[]>([]);
  useEffect(() => {
    webRequest<{ skills: SkillEntry[] }>(ReqMethod.SKILLS_LIST)
      .then((r) => setSkills(r.skills || []))
      .catch(() => {});
  }, []);
  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <h2 className="mb-6 text-lg font-semibold text-paper-900">技能</h2>
      {skills.length === 0 ? (
        <p className="text-sm text-paper-500">暂无技能</p>
      ) : (
        <ul className="divide-y divide-paper-200 rounded-xl border border-paper-300 bg-paper-50">
          {skills.map((s) => (
            <li key={s.name} className="px-4 py-3">
              <div className="text-sm font-medium text-paper-900">{s.name}</div>
              {s.description && (
                <div className="mt-0.5 text-xs text-paper-600">{s.description}</div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
