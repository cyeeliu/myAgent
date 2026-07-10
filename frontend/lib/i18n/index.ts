// Lightweight i18n — a tiny `t(key, vars)` with zh/en locales and lazy
// language detection from the browser. No external dep (keeps the frontend
// bundle small); the webClient only needs a handful of network/error strings.
// Swap for i18next later without changing call sites: `t('network.wsError')`.
import zh from './locales/zh.json';
import en from './locales/en.json';

export type Locale = 'zh' | 'en';

const DICTS: Record<Locale, Record<string, unknown>> = { zh, en };

function detect(): Locale {
  if (typeof navigator === 'undefined') return 'zh';
  const lang = (navigator.language || 'zh').toLowerCase();
  return lang.startsWith('en') ? 'en' : 'zh';
}

let current: Locale = detect();

export function setLocale(loc: Locale): void {
  current = loc;
}

export function getLocale(): Locale {
  return current;
}

function lookup(dict: Record<string, unknown>, path: string): unknown {
  let node: unknown = dict;
  for (const part of path.split('.')) {
    if (node && typeof node === 'object' && part in (node as Record<string, unknown>)) {
      node = (node as Record<string, unknown>)[part];
    } else {
      return undefined;
    }
  }
  return node;
}

/** Translate `network.wsError` etc., interpolating `{{var}}` from `vars`. */
export function t(path: string, vars?: Record<string, string | number>): string {
  const raw = lookup(DICTS[current], path);
  if (typeof raw !== 'string') return path;
  if (!vars) return raw;
  return raw.replace(/\{\{(\w+)\}\}/g, (_, k) =>
    vars[k] !== undefined ? String(vars[k]) : `{{${k}}}`);
}

const i18n = { t, setLocale, getLocale };
export default i18n;
