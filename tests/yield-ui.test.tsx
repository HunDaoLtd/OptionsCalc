/** @format */
// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n, { syncDocumentLanguage } from "../src/i18n";
import { YieldPage } from "../src/main";

beforeEach(async () => {
  window.localStorage.clear();
  await i18n.changeLanguage("en");
  syncDocumentLanguage("en");
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("yield page localization", () => {
  it("localizes controls, stale data, technical errors, and empty state without refetching", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      underlying: "BTC",
      indexPrice: 62_345.5,
      serverTime: Date.now(),
      updatedAt: Date.now(),
      contracts: [],
      stale: true,
      sourceError: "Binance request failed"
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<YieldPage />);

    expect(await screen.findByText("Reference")).not.toBeNull();
    expect(screen.getByText("Showing last successful snapshot")).not.toBeNull();
    expect(screen.getByText("Market data error: Binance request failed")).not.toBeNull();
    expect(screen.getByText("No BTC Put contracts to display")).not.toBeNull();

    await user.click(screen.getByRole("button", { name: "Chinese" }));
    await waitFor(() => expect(screen.getByText("参考价格")).not.toBeNull());
    expect(screen.getByText("当前是最近一次成功快照")).not.toBeNull();
    expect(screen.getByText("行情数据异常：Binance request failed")).not.toBeNull();
    expect(screen.getByText("暂无可显示的 BTC 看跌期权合约")).not.toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("updates elapsed time without refetching or resetting controls", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-02T12:00:00+08:00"));
    const updatedAt = Date.now() - 1000;
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      underlying: "BTC",
      indexPrice: 62_345.5,
      serverTime: Date.now(),
      updatedAt,
      contracts: [],
      stale: false,
      sourceError: null
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    render(<YieldPage />);

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    const margin = screen.getByLabelText("Margin / Contract") as HTMLInputElement;
    fireEvent.change(margin, { target: { value: "12345" } });
    expect(screen.getByText(/Updated just now/)).not.toBeNull();

    await act(async () => vi.advanceTimersByTimeAsync(5000));

    expect(screen.getByText(/Updated 6s ago/)).not.toBeNull();
    expect(margin.value).toBe("12345");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
