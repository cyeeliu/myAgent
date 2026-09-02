/**
 * SkillHub（www.skillhub.cn）在线检索弹窗：从 SkillHub 检索并安装技能。
 * 后端：agent_gateway/skill_marketplaces.py 的 skillhub_* 函数。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { webRequest } from "../../services/webClient";

/** 与后端 _SKILLHUB_SITE 一致（info 请求失败时的回退） */
const DEFAULT_SKILLHUB_BASE_URL = "https://www.skillhub.cn";

type LoadState = "idle" | "loading" | "success" | "error";

const avatarColors = [
  "bg-red-500", "bg-orange-500", "bg-amber-500", "bg-yellow-500",
  "bg-lime-500", "bg-green-500", "bg-emerald-500", "bg-teal-500",
  "bg-cyan-500", "bg-sky-500", "bg-blue-500", "bg-indigo-500",
  "bg-violet-500", "bg-purple-500", "bg-fuchsia-500", "bg-pink-500",
  "bg-rose-500",
];

const getSkillAvatar = (name: string) => {
  const firstChar = (name || "?").charAt(0).toUpperCase();
  const colorIndex = (name || " ").charCodeAt(0) % avatarColors.length;
  return { firstChar, color: avatarColors[colorIndex] };
};

type SkillHubSkillItem = {
  asset_id: string;
  name: string;
  display_name: string;
  summary: string;
  version: string;
  updated_at: number;
  stars?: number;
  downloads?: number;
  owner?: string;
};

interface SkillHubSearchModalProps {
  open: boolean;
  embedded?: boolean;
  sessionId: string;
  /** 外部传入的搜索关键词 */
  externalSearchQuery?: string;
  installedSkillNames?: ReadonlySet<string>;
  /** 视图模式：列表或平铺 */
  viewMode?: "list" | "grid";
  onClose: () => void;
  onInstalled?: (skillName: string) => void | Promise<void>;
}

export function SkillHubSearchModal({
  open,
  embedded = false,
  sessionId,
  externalSearchQuery,
  installedSkillNames,
  viewMode = "list",
  onClose,
  onInstalled,
}: SkillHubSearchModalProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SkillHubSkillItem[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [installingAssetId, setInstallingAssetId] = useState<string | null>(null);
  const [installedNames, setInstalledNames] = useState<Set<string>>(new Set());
  const [hubBaseUrl, setHubBaseUrl] = useState(DEFAULT_SKILLHUB_BASE_URL);
  const messageTimerRef = useRef<number | null>(null);

  const withSession = useCallback(
    (params?: Record<string, unknown>) => ({
      ...(params || {}),
      session_id: sessionId,
    }),
    [sessionId]
  );

  const showMessage = useCallback((type: "success" | "error", text: string) => {
    if (messageTimerRef.current !== null) {
      window.clearTimeout(messageTimerRef.current);
      messageTimerRef.current = null;
    }
    setMessage({ type, text });
    messageTimerRef.current = window.setTimeout(() => {
      setMessage(null);
      messageTimerRef.current = null;
    }, 3000);
  }, []);

  useEffect(
    () => () => {
      if (messageTimerRef.current !== null) {
        window.clearTimeout(messageTimerRef.current);
        messageTimerRef.current = null;
      }
    },
    []
  );

  useEffect(() => {
    if (!open) return;
    setInstalledNames(new Set());
    setHubBaseUrl(DEFAULT_SKILLHUB_BASE_URL);
  }, [open]);

  useEffect(() => {
    if (embedded && externalSearchQuery !== undefined) {
      setQuery(externalSearchQuery);
    }
  }, [externalSearchQuery, embedded]);

  // 打开即搜索：空查询时后端返回 score 榜单，无需用户先输入关键词。
  // 300ms 防抖 + 竞态保护：按键期间合并请求，丢弃过期响应。
  const searchReqIdRef = useRef(0);
  useEffect(() => {
    if (!embedded) return;
    const timer = window.setTimeout(() => {
      const reqId = ++searchReqIdRef.current;
      const q = query.trim();
      setLoadState("loading");
      setMessage(null);
      void (async () => {
        try {
          const data = await webRequest<{
            success: boolean;
            detail?: string;
            skills?: SkillHubSkillItem[];
          }>("skills.skillhub.search", withSession({ q, limit: 50 }));
          if (reqId !== searchReqIdRef.current) return;
          if (!data.success) {
            throw new Error(data.detail || t("skills.skillhub.errors.searchFailed"));
          }
          setResults(data.skills || []);
          setLoadState("success");
        } catch (error) {
          if (reqId !== searchReqIdRef.current) return;
          console.error(error);
          setResults([]);
          setLoadState("error");
          showMessage(
            "error",
            error instanceof Error ? error.message : t("skills.skillhub.errors.searchFailed")
          );
        }
      })();
    }, 300);
    return () => window.clearTimeout(timer);
  }, [query, embedded, showMessage, t, withSession]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void (async () => {
      try {
        const data = await webRequest<{
          success?: boolean;
          market_base_url?: string;
        }>("skills.skillhub.info", withSession());
        const url = data.market_base_url?.trim();
        if (!cancelled && data.success && url) {
          try {
            setHubBaseUrl(new URL(url).href.replace(/\/$/, ""));
          } catch {
            setHubBaseUrl(url.replace(/\/$/, ""));
          }
        }
      } catch {
        if (!cancelled) setHubBaseUrl(DEFAULT_SKILLHUB_BASE_URL);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, withSession]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    if (open) {
      window.addEventListener("keydown", handleKeyDown);
      return () => window.removeEventListener("keydown", handleKeyDown);
    }
  }, [open, onClose]);

  const handleSearch = useCallback(async () => {
    const q = query.trim();
    setLoadState("loading");
    setMessage(null);
    try {
      const data = await webRequest<{
        success: boolean;
        detail?: string;
        skills?: SkillHubSkillItem[];
      }>("skills.skillhub.search", withSession({ q, limit: 50 }));
      if (!data.success) {
        throw new Error(data.detail || t("skills.skillhub.errors.searchFailed"));
      }
      setResults(data.skills || []);
      setLoadState("success");
    } catch (error) {
      console.error(error);
      setResults([]);
      setLoadState("error");
      showMessage(
        "error",
        error instanceof Error ? error.message : t("skills.skillhub.errors.searchFailed")
      );
    }
  }, [query, showMessage, t, withSession]);

  const handleInstall = useCallback(
    async (item: SkillHubSkillItem) => {
      if (installingAssetId) return;
      setInstallingAssetId(item.asset_id);
      setMessage(null);
      try {
        const data = await webRequest<{
          success: boolean;
          detail?: string;
          skill?: { name: string };
        }>("skills.skillhub.install", withSession({
          asset_id: item.asset_id,
          force: false,
          meta: {
            source: "skillhub",
            version: item.version || "",
            author: item.owner || "",
            summary: item.summary || "",
            url: `https://www.skillhub.cn/skill/${item.asset_id}`,
          },
        }));
        if (!data.success) {
          throw new Error(data.detail || t("skills.skillhub.errors.installFailed"));
        }
        const skillName = data.skill?.name || item.name;
        setInstalledNames((prev) => new Set([...prev, skillName]));
        showMessage("success", t("skills.skillhub.messages.installed", { name: skillName }));
        await onInstalled?.(skillName);
      } catch (error) {
        console.error(error);
        showMessage(
          "error",
          error instanceof Error ? error.message : t("skills.skillhub.errors.installFailed")
        );
      } finally {
        setInstallingAssetId(null);
      }
    },
    [installingAssetId, onInstalled, showMessage, t, withSession]
  );

  if (!open) return null;

  const itemTitle = (item: SkillHubSkillItem) => item.display_name || item.name;

  const renderStats = (item: SkillHubSkillItem) => (
    <div className="flex flex-wrap gap-2 text-xs text-text-muted mt-1">
      {typeof item.stars === "number" && (
        <span className="inline-flex items-center gap-1">
          <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 24 24" aria-hidden>
            <path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z" />
          </svg>
          {item.stars}
        </span>
      )}
      {typeof item.downloads === "number" && (
        <span>↓ {item.downloads}</span>
      )}
      {item.owner && <span className="truncate max-w-[140px]">@{item.owner}</span>}
    </div>
  );

  if (embedded) {
    return (
      <div className="flex flex-col h-full">
        <div className="overflow-auto flex-1 min-h-0">
          {message && message.type === "success" && (
            <div className="fixed top-4 right-4 z-[9999] rounded-[4px] text-sm text-black shadow-lg flex items-center gap-3 px-4" style={{ backgroundColor: "#d5f2dc", width: "564px", height: "40px" }}>
              <span className="w-4 h-4 rounded-full bg-[#1a991d] flex items-center justify-center flex-shrink-0">
                <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                </svg>
              </span>
              {message.text.replace("√", "")}
              <button
                type="button"
                onClick={() => showMessage("success", "")}
                className="ml-auto w-6 h-6 flex items-center justify-center hover:bg-white/30 rounded-full transition-colors"
              >
                <svg className="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          )}
          {message && message.type === "error" && (
            <div className="mb-3 px-3 py-2.5 rounded-lg text-sm leading-snug border border-danger/40 bg-danger/10 text-danger">
              {message.text}
            </div>
          )}

          {loadState === "loading" && (
            <div className="flex items-center justify-center h-full text-text-muted">{t("common.loading")}</div>
          )}
          {loadState === "error" && (
            <div className="text-sm text-text-muted">{t("skills.skillhub.errors.searchFailed")}</div>
          )}
          {loadState === "success" && (
            <div className={`mt-4 flex-1 min-h-0 overflow-y-auto ${viewMode === "grid" ? "flex flex-wrap gap-4 content-start" : "space-y-3"}`}>
                {results.length === 0 ? (
                  <div className="text-sm text-text-muted">{t("skills.skillhub.noResults")}</div>
                ) : (
                  results.map((item) => {
                    const isInstalled =
                      installedNames.has(item.name) || (installedSkillNames?.has(item.name) ?? false);
                    const isInstalling = installingAssetId === item.asset_id;
                    const avatar = getSkillAvatar(itemTitle(item));
                    return (
                      <div
                        key={item.asset_id}
                        className={`p-4 rounded-lg border border-border bg-panel ${viewMode === "grid" ? "flex flex-col" : "flex items-start justify-between gap-4"}`}
                        style={viewMode === "grid" ? { width: "496px", height: "168px", flexShrink: 0 } : undefined}
                      >
                        {viewMode === "list" ? (
                          <>
                            <div className="flex items-center gap-3 min-w-0 flex-1">
                              <div className={`w-10 h-10 rounded-lg ${avatar.color} flex items-center justify-center flex-shrink-0 text-white font-semibold`}>
                                {avatar.firstChar}
                              </div>
                              <div className="min-w-0 flex-1">
                                <div className="text-base font-semibold text-text-strong truncate">
                                  {itemTitle(item)}
                                </div>
                                <div className="text-sm text-text-muted mt-1 line-clamp-3">
                                  {item.summary || t("skills.noDescription")}
                                </div>
                                {renderStats(item)}
                              </div>
                            </div>
                            <div className="flex flex-col items-end gap-2 flex-shrink-0">
                              {isInstalled ? (
                                <span className="px-4 h-[28px] flex items-center rounded-2xl text-sm whitespace-nowrap border border-[color:var(--border-ok)] bg-ok-subtle text-ok">
                                  {t("skills.status.installed")}
                                </span>
                              ) : (
                                <button
                                  type="button"
                                  onClick={() => void handleInstall(item)}
                                  disabled={isInstalling}
                                  className={`min-w-[76px] h-[28px] px-3 rounded-[24px] text-sm text-[#191919] border border-[#191919] hover:bg-secondary/50 transition-colors whitespace-nowrap ${
                                    isInstalling
                                      ? "text-text-muted cursor-not-allowed"
                                      : "text-text"
                                  }`}
                                >
                                  {isInstalling
                                    ? t("skills.skillhub.installing")
                                    : t("skills.actions.install")}
                                </button>
                              )}
                            </div>
                          </>
                        ) : (
                          <>
                            <div className="flex items-start gap-3 flex-shrink-0">
                              <div className={`w-10 h-10 rounded-lg ${avatar.color} flex items-center justify-center flex-shrink-0 text-white font-semibold text-sm`}>
                                {avatar.firstChar}
                              </div>
                              <div className="min-w-0 flex-1">
                                <div className="text-sm font-semibold text-text-strong truncate">
                                  {itemTitle(item)}
                                </div>
                                <div className="text-xs text-text-muted mt-1 line-clamp-2">
                                  {item.summary || t("skills.noDescription")}
                                </div>
                              </div>
                            </div>
                            <div className="flex flex-wrap gap-1.5 mt-2 flex-shrink-0 text-xs text-text-muted">
                              <span className="px-2 py-0.5 rounded-full bg-secondary border border-border truncate">
                                {t("skills.versionLabel")}: {item.version || "latest"}
                              </span>
                            </div>
                            <div className="flex items-center mt-auto pt-2 gap-2 flex-shrink-0" style={{ width: "100%" }}>
                              <div className="flex gap-1.5 flex-1">
                              </div>
                              <div className="flex-shrink-0 ml-auto">
                                {isInstalled ? (
                                  <span className="px-4 h-[28px] flex items-center rounded-2xl text-sm whitespace-nowrap border border-[color:var(--border-ok)] bg-ok-subtle text-ok">
                                    {t("skills.status.installed")}
                                  </span>
                                ) : (
                                  <button
                                    type="button"
                                    onClick={() => void handleInstall(item)}
                                    disabled={isInstalling}
                                    className={`min-w-[76px] h-[28px] px-3 rounded-[24px] text-sm text-[#191919] border border-[#191919] hover:bg-secondary/50 transition-colors whitespace-nowrap ${
                                      isInstalling
                                        ? "text-text-muted cursor-not-allowed"
                                        : "text-text"
                                    }`}
                                  >
                                    {isInstalling ? t("skills.skillhub.installing") : t("skills.actions.install")}
                                  </button>
                                )}
                              </div>
                            </div>
                          </>
                        )}
                      </div>
                    );
                  })
                )}
              </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
        aria-label={t("common.close")}
      />
      <div className="relative w-full max-w-2xl max-h-[85vh] overflow-hidden rounded-xl border border-border bg-card shadow-2xl animate-rise flex flex-col">
        <div className="flex items-start justify-between gap-3 px-5 py-3 border-b border-border bg-panel flex-shrink-0">
          <div className="min-w-0 flex-1 space-y-1">
            <h3 className="text-base font-semibold text-text">{t("skills.skillhub.title")}</h3>
            <p className="text-[11px] leading-snug text-text-muted">
              <a
                href={hubBaseUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-accent underline decoration-accent/35 underline-offset-2 hover:text-accent-hover hover:decoration-accent/60"
                aria-label={t("skills.skillhub.titleHubAria")}
              >
                {t("skills.skillhub.titleHubLinkText")}
              </a>
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded-2xl text-sm text-text border border-gray-400 hover:border-gray-600 hover:bg-secondary/50 transition-colors"
          >
            {t("common.close")}
          </button>
        </div>

        <div className="p-5 overflow-auto flex-1 min-h-0">
          {message && message.type === "success" && (
            <div className="fixed top-4 right-4 z-[9999] rounded-[4px] text-sm text-black shadow-lg flex items-center gap-3 px-4" style={{ backgroundColor: "#d5f2dc", width: "564px", height: "40px" }}>
              <span className="w-4 h-4 rounded-full bg-[#1a991d] flex items-center justify-center flex-shrink-0">
                <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                </svg>
              </span>
              {message.text.replace("√", "")}
              <button
                type="button"
                onClick={() => showMessage("success", "")}
                className="ml-auto w-6 h-6 flex items-center justify-center hover:bg-white/30 rounded-full transition-colors"
              >
                <svg className="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          )}
          {message && message.type === "error" && (
            <div className="mb-3 px-3 py-2.5 rounded-lg text-sm leading-snug border border-danger/40 bg-danger/10 text-danger">
              {message.text}
            </div>
          )}

          <div className="flex items-center gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              placeholder={t("skills.skillhub.searchPlaceholder")}
              className="flex-1 min-w-0 px-3 py-2 rounded-md bg-secondary border border-border text-sm text-text placeholder:text-text-muted"
            />
            <button
              type="button"
              onClick={() => void handleSearch()}
              disabled={loadState === "loading"}
              className={`px-4 py-2 rounded-2xl text-sm transition-colors border border-gray-400 hover:border-gray-600 hover:bg-secondary/50 ${
                loadState === "loading"
                  ? "text-text-muted cursor-not-allowed"
                  : "text-text"
              }`}
            >
              {loadState === "loading" ? t("common.loading") : t("skills.skillhub.search")}
            </button>
          </div>

          {loadState === "success" && (
            <div className="mt-4 flex min-h-0 max-h-[50vh] flex-col gap-2">
              <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-0.5">
                {results.length === 0 ? (
                  <div className="text-xs text-text-muted">{t("skills.skillhub.noResults")}</div>
                ) : (
                  results.map((item) => {
                    const isInstalled =
                      installedNames.has(item.name) || (installedSkillNames?.has(item.name) ?? false);
                    const isInstalling = installingAssetId === item.asset_id;
                    const avatar = getSkillAvatar(itemTitle(item));
                    return (
                      <div
                        key={item.asset_id}
                        className="p-4 rounded-lg border border-border bg-panel flex items-start justify-between gap-4"
                      >
                        <div className="flex items-center gap-3 min-w-0 flex-1">
                          <div className={`w-10 h-10 rounded-lg ${avatar.color} flex items-center justify-center flex-shrink-0 text-white font-semibold`}>
                            {avatar.firstChar}
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="text-base font-semibold text-text-strong truncate">
                              {itemTitle(item)}
                            </div>
                            <div className="text-sm text-text-muted mt-1 line-clamp-3">
                              {item.summary || t("skills.noDescription")}
                            </div>
                            {renderStats(item)}
                            <div className="text-xs text-text-muted mt-1">
                              {t("skills.versionLabel")}: {item.version || "latest"}
                            </div>
                          </div>
                        </div>
                        <div className="flex-shrink-0">
                          {isInstalled ? (
                              <span className="px-4 py-2 rounded-2xl text-sm whitespace-nowrap border border-[color:var(--border-ok)] bg-ok-subtle text-ok">
                                {t("skills.status.installed")}
                              </span>
                            ) : (
                              <button
                                type="button"
                                onClick={() => void handleInstall(item)}
                                disabled={isInstalling}
                                className={`min-w-[76px] h-[28px] px-3 rounded-[24px] text-sm text-[#191919] border border-[#191919] hover:bg-secondary/50 transition-colors whitespace-nowrap ${
                                  isInstalling
                                    ? "text-text-muted cursor-not-allowed"
                                    : "text-text"
                                }`}
                              >
                                {isInstalling
                                  ? t("skills.skillhub.installing")
                                  : t("skills.actions.install")}
                              </button>
                            )}
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
