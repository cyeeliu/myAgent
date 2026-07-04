"use client";
import { useState } from "react";

// Strip ANSI color codes from tool output (spec §5).
const ANSI = /\[[0-9;]*m/g;
function stripAnsi(s: string) { return s.replace(ANSI, ""); }

export function ToolCard({ name, input, result, blocked }: {
  name: string;
  input: Record<string, any>;
  result?: string;
  blocked?: boolean;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="rounded border border-zinc-700 bg-zinc-900 text-sm">
      <button className="flex w-full items-center gap-2 px-3 py-2 text-left" onClick={() => setOpen(!open)}>
        <span className="text-cyan-400">🔧 {name}</span>
        {blocked && <span className="text-red-400">(blocked)</span>}
        {!result && !blocked && <span className="text-zinc-500">running…</span>}
        <span className="ml-auto text-zinc-500">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="space-y-2 px-3 pb-3 font-mono text-xs">
          <div><span className="text-zinc-500">input:</span> {JSON.stringify(input)}</div>
          {result !== undefined && (
            <div><span className="text-zinc-500">output:</span>
              <pre className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap text-zinc-300">{stripAnsi(result)}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
