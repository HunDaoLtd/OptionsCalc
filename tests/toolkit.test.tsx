/** @format */
// @vitest-environment jsdom

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n, { getNumberFormatter, LANGUAGE_STORAGE_KEY, resolveInitialLanguage, syncDocumentLanguage } from "../src/i18n";
import { formatNumber, ToolkitHeader, UpdatedAgo, useTheme } from "../src/toolkit";

beforeEach(async () => {
  window.localStorage.clear();
  await i18n.changeLanguage("en");
  syncDocumentLanguage("en");
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

function ThemeHarness() {
  const { theme, setTheme } = useTheme();
  return (
    <main data-theme={theme}>
      <ToolkitHeader
        activePage="risk"
        theme={theme}
        onThemeChange={setTheme}
        loading={false}
        onRefresh={() => undefined}
      />
    </main>
  );
}

function UpdatedAgoHarness({ timestamp, onRender }: { timestamp: number; onRender: () => void }) {
  onRender();
  return <span data-testid="updated"><UpdatedAgo timestamp={timestamp} showAbsoluteTime /></span>;
}

describe("shared toolkit shell", () => {
  it("resolves stored and browser languages with an English fallback", () => {
    expect(resolveInitialLanguage("zh", ["en-US"])).toBe("zh");
    expect(resolveInitialLanguage("en", ["zh-CN"])).toBe("en");
    expect(resolveInitialLanguage("invalid", ["zh-Hans-CN", "en-US"])).toBe("zh");
    expect(resolveInitialLanguage(null, ["fr-FR"])).toBe("en");
  });

  it("reuses number formatters by locale and precision without changing formatted output", async () => {
    const englishTwoDigits = getNumberFormatter(2);
    expect(getNumberFormatter(2)).toBe(englishTwoDigits);
    expect(englishTwoDigits.format(1234.5)).toBe("1,234.50");
    expect(formatNumber(null)).toBe("—");

    await i18n.changeLanguage("zh");
    const chineseTwoDigits = getNumberFormatter(2);
    expect(chineseTwoDigits).not.toBe(englishTwoDigits);
    expect(getNumberFormatter(2)).toBe(chineseTwoDigits);
    expect(getNumberFormatter(1)).not.toBe(chineseTwoDigits);
  });

  it("updates relative time without rerendering its parent", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-02T12:00:00+08:00"));
    const parentRender = vi.fn();

    render(<UpdatedAgoHarness timestamp={Date.now() - 1000} onRender={parentRender} />);

    expect(screen.getByTestId("updated").textContent).toContain("Updated just now");
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(screen.getByTestId("updated").textContent).toContain("Updated 6s ago");
    expect(parentRender).toHaveBeenCalledTimes(1);
  });

  it("restores and persists all four shared themes", async () => {
    window.localStorage.setItem("btc-put-yield-theme", "citrus");
    const user = userEvent.setup();
    const { container } = render(<ThemeHarness />);
    const shell = container.querySelector("main")!;

    expect(shell.getAttribute("data-theme")).toBe("citrus");
    for (const [label, value] of [
      ["Pop theme", "pop"],
      ["Tropical theme", "tropical"],
      ["Citrus theme", "citrus"],
      ["Berry theme", "berry"],
    ] as const) {
      await user.click(screen.getByRole("button", { name: label }));
      expect(shell.getAttribute("data-theme")).toBe(value);
      expect(window.localStorage.getItem("btc-put-yield-theme")).toBe(value);
    }
  });

  it("keeps Yield public navigation separate from the protected Risk path", () => {
    render(<ThemeHarness />);

    const yieldLink = screen.getByRole("link", { name: "Put Yield Matrix" });
    const riskLink = screen.getByRole("link", { name: "Liquidation Risk" });
    const yieldHref = yieldLink.getAttribute("href");
    expect(yieldHref?.endsWith("/")).toBe(true);
    expect(riskLink.getAttribute("href")).toBe(`${yieldHref}risk/`);
    expect(riskLink.classList.contains("active")).toBe(true);
    expect(yieldLink.classList.contains("active")).toBe(false);
  });

  it("switches language without navigation and synchronizes persistent document state", async () => {
    const user = userEvent.setup();
    render(<ThemeHarness />);
    const languageChanged = vi.fn();
    i18n.on("languageChanged", languageChanged);

    const chineseButton = screen.getByRole("button", { name: "Chinese" });
    expect(chineseButton.textContent).toBe("汉");
    const englishButton = screen.getByRole("button", { name: "English" });
    expect(englishButton.textContent).toBe("EN");
    await user.click(englishButton);
    expect(languageChanged).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("en");
    expect(document.documentElement.lang).toBe("en");
    expect(document.title).toBe("BTC Options Toolkit");

    await user.click(chineseButton);
    await waitFor(() => expect(screen.getByRole("link", { name: "看跌期权收益矩阵" })).not.toBeNull());
    expect(languageChanged).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("zh");
    expect(document.documentElement.lang).toBe("zh-CN");
    expect(document.title).toBe("BTC 期权工具");

    await user.click(screen.getByRole("button", { name: "英文" }));
    await waitFor(() => expect(screen.getByRole("link", { name: "Put Yield Matrix" })).not.toBeNull());
    expect(languageChanged).toHaveBeenCalledTimes(2);
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("en");
    expect(document.documentElement.lang).toBe("en");
    expect(document.title).toBe("BTC Options Toolkit");
    i18n.off("languageChanged", languageChanged);
  });
});
