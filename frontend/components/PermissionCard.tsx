"use client";

export function PermissionCard({ reason, detail, onRespond }: {
  reason: string;
  detail?: string;
  onRespond: (allow: boolean) => void;
}) {
  return (
    <div className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm shadow-soft">
      <div className="flex items-center gap-2 font-medium text-amber-800">
        <span>⚠</span>
        <span>请求授权：{reason}</span>
      </div>
      {detail && (
        <pre className="mt-2.5 whitespace-pre-wrap rounded-lg bg-amber-100/60 p-2.5 font-mono text-xs text-amber-900/80">{detail}</pre>
      )}
      <div className="mt-3.5 flex gap-2">
        <button className="rounded-lg bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white transition hover:bg-emerald-700"
                onClick={() => onRespond(true)}>允许</button>
        <button className="rounded-lg border border-paper-300 px-4 py-1.5 text-sm text-paper-700 transition hover:bg-paper-200"
                onClick={() => onRespond(false)}>拒绝</button>
      </div>
    </div>
  );
}
