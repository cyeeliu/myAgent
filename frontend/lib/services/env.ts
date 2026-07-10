// Gateway base URL resolution. Same-origin by default (behind nginx the API is
// on the same origin as the page); falls back to localhost:8000 for `next dev`.
// Mirrors lib/sessions.ts GATEWAY but centralized so the webClient can build a
// ws:// URL without duplicating the convention.
export const GATEWAY_HTTP =
  process.env.NEXT_PUBLIC_GATEWAY_URL ||
  (typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8000');

export function getWsBase(): string {
  // If an explicit gateway URL is set, derive ws scheme from it; otherwise use
  // the page's own host (same-origin behind nginx).
  const explicit = process.env.NEXT_PUBLIC_GATEWAY_URL;
  if (explicit) {
    return explicit.replace(/^http/, 'ws');
  }
  if (typeof window !== 'undefined') {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}`;
  }
  return 'ws://localhost:8000';
}
