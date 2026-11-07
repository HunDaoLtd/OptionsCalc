import i18n, { type TOptions } from "i18next";
import { initReactI18next } from "react-i18next";
import { en } from "./locales/en";
import { zh } from "./locales/zh";

export type Language = "zh" | "en";

export const LANGUAGE_STORAGE_KEY = "btc-options-language";
export const SUPPORTED_LANGUAGES: readonly Language[] = ["zh", "en"];

function storedLanguage() {
  try {
    return window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function resolveInitialLanguage(stored: string | null, browserLanguages: readonly string[]): Language {
  if (SUPPORTED_LANGUAGES.includes(stored as Language)) return stored as Language;
  return browserLanguages.some((language) => language.toLowerCase().startsWith("zh")) ? "zh" : "en";
}

export function activeLanguage(): Language {
  return i18n.resolvedLanguage?.startsWith("zh") ? "zh" : "en";
}

export function activeLocale() {
  return activeLanguage() === "zh" ? "zh-CN" : "en-US";
}

const numberFormatters = new Map<string, Intl.NumberFormat>();

export function getNumberFormatter(digits = 0) {
  const locale = activeLocale();
  const key = `${locale}:${digits}`;
  const cached = numberFormatters.get(key);
  if (cached) return cached;
  const formatter = new Intl.NumberFormat(locale, {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits
  });
  numberFormatters.set(key, formatter);
  return formatter;
}

export function tRisk(key: string, options?: TOptions) {
  return String(i18n.t(key, { ns: "risk", ...options }));
}

const initialLanguage = resolveInitialLanguage(
  typeof window === "undefined" ? null : storedLanguage(),
  typeof navigator === "undefined" ? [] : navigator.languages
);

void i18n.use(initReactI18next).init({
  resources: {
    en,
    zh
  },
  lng: initialLanguage,
  fallbackLng: "en",
  supportedLngs: [...SUPPORTED_LANGUAGES],
  defaultNS: "common",
  keySeparator: false,
  interpolation: { escapeValue: false },
  returnNull: false
});

export function syncDocumentLanguage(language: Language) {
  if (typeof document === "undefined") return;
  document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  document.title = String(i18n.t("app.title", { ns: "common", lng: language }));
}

syncDocumentLanguage(initialLanguage);

export async function changeLanguage(language: Language) {
  if (!i18n.resolvedLanguage || activeLanguage() !== language) {
    await i18n.changeLanguage(language);
  }
  try {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  } catch {
    // Language switching still works when storage is unavailable.
  }
  syncDocumentLanguage(language);
}

export default i18n;
