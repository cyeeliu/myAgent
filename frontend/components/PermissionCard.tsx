"use client";

// Inline allow/deny card for a permission_request. Response goes back via the
// current transport (WS permission_response or SSE REST POST) — the parent
// passes `onRespond` which calls transport.send.
export function PermissionCard({ reason, detail, onRespond }: {
  reason: string;
  detail?: string;
  onRespond: (allow: boolean) => void;
}) {
  return (
    <div className="my-2 rounded border border-amber-700 bg-amber-950/40 p-3 text-sm">
      <div className="font-semibold text-amber-300">⚠ Permission requested: {reason}</div>
      {detail && <pre className="mt-1 whitespace-pre-wrap font-mono text-xs text-amber-200">{detail}</pre>}
      <div className="mt-2 flex gap-2">
        <button className="rounded bg-emerald-700 px-3 py-1 text-white hover:bg-emerald-600"
                onClick={() => onRespond(true)}>Allow</button>
        <button className="rounded bg-red-700 px-3 py-1 text-white hover:bg-red-600"
                onClick={() => onRespond(false)}>Deny</button>
      </div>
    </div>
  );
}
