"use client";
import { useState } from "react";

const ANSI = /\[[0-9;]*m/g;
function stripAnsi(s: string) { return s.replace(ANSI, ""); }

function CopyBtn({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="text-paper-500 transition hover:text-paper-800"
      onClick={async (e) => {
        e.stopPropagation();
        try { await navigator.clipboard?.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1200); } catch {}
      }}
    >{copied ? "已复制" : "复制"}</button>
  );
}

function DiffView({ oldText, newText }: { oldText?: string; newText: string }) {
  if (!oldText) {
    // write_file case: show full content
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-paper-500">new content</span>
          <CopyBtn text={newText} />
        </div>
        <pre className="max-h-60 overflow-y-auto whitespace-pre-wrap rounded-lg bg-paper-150 p-2.5 text-paper-700">{newText}</pre>
      </div>
    );
  }

  // edit_file case: show diff
  const oldLines = oldText.split("\n");
  const newLines = newText.split("\n");
  const maxLines = Math.max(oldLines.length, newLines.length);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-paper-500">diff (old → new)</span>
        <CopyBtn text={newText} />
      </div>
      <pre className="max-h-60 overflow-y-auto whitespace-pre-wrap rounded-lg bg-paper-150 p-2.5 text-paper-700 text-xs">
        {Array.from({ length: maxLines }).map((_, i) => {
          const oldLine = oldLines[i];
          const newLine = newLines[i];
          if (oldLine === newLine) {
            return <div key={i} className="text-paper-600">  {oldLine || ""}</div>;
          } else if (oldLine === undefined) {
            return <div key={i} className="text-emerald-700 bg-emerald-50">+ {newLine}</div>;
          } else if (newLine === undefined) {
            return <div key={i} className="text-red-700 bg-red-50">- {oldLine}</div>;
          } else {
            return (
              <div key={i} className="space-y-1">
                <div className="text-red-700 bg-red-50">- {oldLine}</div>
                <div className="text-emerald-700 bg-emerald-50">+ {newLine}</div>
              </div>
            );
          }
        })}
      </pre>
    </div>
  );
}

export function ToolCard({ name, input, result, blocked }: {
  name: string;
  input: Record<string, any>;
  result?: string;
  blocked?: boolean;
}) {
  const [open, setOpen] = useState(true);
  const running = result === undefined && !blocked;

  // Check if this is an edit_file or write_file tool
  const isEditFile = name === "edit_file";
  const isWriteFile = name === "write_file";
  const showDiff = (isEditFile || isWriteFile) && result !== undefined && !blocked;

  // Extract old_text and new_text for edit_file
  const oldText = isEditFile ? input.old_string : undefined;
  const newText = isEditFile ? input.new_string : isWriteFile ? input.content : result;

  return (
    <div className="overflow-hidden rounded-xl border border-paper-300 bg-paper-50 text-sm shadow-soft">
      <button
        className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left transition hover:bg-paper-150"
        onClick={() => setOpen(!open)}
      >
        <span className={`h-2 w-2 shrink-0 rounded-full ${
          blocked ? "bg-red-500" : running ? "bg-clay-500 animate-pulse" : "bg-emerald-500"
        }`} />
        <span className="font-mono text-[13px] text-paper-900">{name}</span>
        {blocked && <span className="text-xs text-red-600">已阻止</span>}
        {running && <span className="text-xs text-paper-500">运行中…</span>}
        <span className="ml-auto text-xs text-paper-500">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-paper-300/70 px-3.5 py-3 font-mono text-xs">
          <div className="flex items-center justify-between gap-2">
            <span className="shrink-0 text-paper-500">input</span>
            <span className="truncate text-paper-700">{JSON.stringify(input)}</span>
          </div>
          {result !== undefined && !showDiff && (
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <span className="text-paper-500">output</span>
                <CopyBtn text={stripAnsi(result)} />
              </div>
              <pre className="max-h-60 overflow-y-auto whitespace-pre-wrap rounded-lg bg-paper-150 p-2.5 text-paper-700">{stripAnsi(result)}</pre>
            </div>
          )}
          {showDiff && (
            <DiffView oldText={oldText} newText={newText} />
          )}
        </div>
      )}
    </div>
  );
}
