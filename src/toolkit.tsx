/** @format */

import React from "react";
import { LogOut, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import i18n, { activeLanguage, activeLocale, changeLanguage, getNumberFormatter } from "./i18n";

const API_BASE = import.meta.env.BASE_URL;
const THEME_STORAGE_KEY = "btc-put-yield-theme";

export type ThemeName = "pop" | "tropical" | "citrus" | "berry";

const THEMES: Array<{ name: ThemeName; label: string }> = [
  { name: "pop", label: "Pop" },
  { name: "tropical", label: "Tropical" },
  { name: "citrus", label: "Citrus" },
  { name: "berry", label: "Berry" }
];

export function useTheme() {
  const [theme, setTheme] = React.useState<ThemeName>(() => {
    const savedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
    return THEMES.some((item) => item.name === savedTheme) ? (savedTheme as ThemeName) : "pop";
  });

  React.useEffect(() => {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  return { theme, setTheme };
}

interface ToolkitHeaderProps {
  activePage: "yield" | "risk";
  theme: ThemeName;
  onThemeChange: (theme: ThemeName) => void;
  loading: boolean;
  onRefresh: () => void;
  sessionAction?: { label: string; onClick: () => void; disabled?: boolean };
}

export function ToolkitHeader({
  activePage,
  theme,
  onThemeChange,
  loading,
  onRefresh,
  sessionAction
}: ToolkitHeaderProps) {
  const { t } = useTranslation("common");
  const language = activeLanguage();

  return (
    <header className="toolkit-header">
      <div className="toolkit-brand">
        <p className="eyebrow">{t("brand.eyebrow")}</p>
        <div className="toolkit-title-line">
          <h1>{t("app.title")}</h1>
          <span className="header-version">v{__APP_VERSION__}</span>
        </div>
      </div>
      <nav className="toolkit-nav" aria-label={t("nav.label")}>
        <a className={activePage === "yield" ? "active" : ""} href={API_BASE}>
          {t("nav.yield")}
        </a>
        <a className={activePage === "risk" ? "active" : ""} href={`${API_BASE}risk/`}>
          {t("nav.risk")}
        </a>
      </nav>
      <div className="top-actions">
        <div className="language-toggle" role="group" aria-label={t("language.label")}>
          <button
            type="button"
            className={language === "zh" ? "active" : ""}
            aria-pressed={language === "zh"}
            aria-label={t("language.zh")}
            onClick={() => void changeLanguage("zh")}
          >
            汉
          </button>
          <button
            type="button"
            className={language === "en" ? "active" : ""}
            aria-pressed={language === "en"}
            aria-label={t("language.en")}
            onClick={() => void changeLanguage("en")}
          >
            EN
          </button>
        </div>
        <div className="theme-toggle" role="group" aria-label={t("theme.label")}>
          {THEMES.map((item) => (
            <button
              key={item.name}
              className={`theme-swatch theme-swatch-${item.name}${theme === item.name ? " active" : ""}`}
              onClick={() => onThemeChange(item.name)}
              aria-label={t("theme.option", { theme: item.label })}
              title={t("theme.option", { theme: item.label })}
            />
          ))}
        </div>
        <button className="icon-button refresh" onClick={onRefresh} title={t("refresh.title")} disabled={loading}>
          <RefreshCw size={18} className={loading ? "spin" : ""} />
          <span>{t("refresh.label")}</span>
        </button>
        {sessionAction ? (
          <button
            className="icon-button session-action"
            onClick={sessionAction.onClick}
            title={sessionAction.label}
            disabled={sessionAction.disabled}
          >
            <LogOut size={17} />
            <span>{sessionAction.label}</span>
          </button>
        ) : null}
      </div>
    </header>
  );
}

interface ToolkitPageShellProps {
  activePage: "yield" | "risk";
  loading: boolean;
  onRefresh: () => void;
  className?: string;
  children: React.ReactNode;
  sessionAction?: { label: string; onClick: () => void; disabled?: boolean };
}

export function ToolkitPageShell({ activePage, loading, onRefresh, className, children, sessionAction }: ToolkitPageShellProps) {
  const { theme, setTheme } = useTheme();
  return (
    <main className={`app-shell${className ? ` ${className}` : ""}`} data-theme={theme}>
      <ToolkitHeader
        activePage={activePage}
        theme={theme}
        onThemeChange={setTheme}
        loading={loading}
        onRefresh={onRefresh}
        sessionAction={sessionAction}
      />
      {children}
    </main>
  );
}

export function formatNumber(value: number | null | undefined, digits = 0) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return getNumberFormatter(digits).format(value);
}

export function formatUpdatedAgo(timestamp: number | null | undefined, now: number) {
  const translate = (key: string, options?: Record<string, number>) =>
    String(i18n.t(key, { ns: "common", ...options }));
  if (!timestamp) return translate("updated.waiting");
  const elapsedSeconds = Math.max(0, Math.floor((now - timestamp) / 1000));
  if (elapsedSeconds < 5) return translate("updated.justNow");
  if (elapsedSeconds < 60) return translate("updated.seconds", { count: elapsedSeconds });
  const elapsedMinutes = Math.floor(elapsedSeconds / 60);
  if (elapsedMinutes < 60) return translate("updated.minutes", { count: elapsedMinutes });
  return translate("updated.hours", { count: Math.floor(elapsedMinutes / 60) });
}

interface UpdatedAgoProps {
  timestamp: number | null | undefined;
  showAbsoluteTime?: boolean;
}

export function UpdatedAgo({ timestamp, showAbsoluteTime = false }: UpdatedAgoProps) {
  useTranslation("common");
  const [now, setNow] = React.useState(Date.now());

  React.useEffect(() => {
    if (!timestamp) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [timestamp]);

  const relative = formatUpdatedAgo(timestamp, now);
  if (!timestamp || !showAbsoluteTime) return <>{relative}</>;
  return <>{relative} ({new Date(timestamp).toLocaleTimeString(activeLocale())})</>;
}
