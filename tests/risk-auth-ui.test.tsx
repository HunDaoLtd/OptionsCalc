/** @format */
// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n, { syncDocumentLanguage } from "../src/i18n";
import { RiskPage } from "../src/risk-page";

function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

beforeEach(async () => {
  await i18n.changeLanguage("en");
  syncDocumentLanguage("en");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("Risk Cookie login flow", () => {
  it("shows the login shell and does not request account data before authentication", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => jsonResponse({ authenticated: false }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<RiskPage />);

    expect(await screen.findByRole("heading", { name: "Unlock Liquidation Risk" })).not.toBeNull();
    expect(screen.getByLabelText("Access password").getAttribute("type")).toBe("password");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/auth\/session$/);

    await user.click(screen.getByRole("button", { name: "Chinese" }));

    expect(await screen.findByRole("heading", { name: "解锁强平风险工具" })).not.toBeNull();
    expect(screen.getByLabelText("访问密码").getAttribute("type")).toBe("password");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("reports wrong passwords and login rate limits without a browser-native prompt", async () => {
    let loginCount = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/session")) return jsonResponse({ authenticated: false });
      loginCount += 1;
      if (loginCount === 1) {
        return jsonResponse({ error: { code: "authentication_required" } }, 401);
      }
      return jsonResponse({ error: { code: "rate_limited" } }, 429);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<RiskPage />);

    const password = await screen.findByLabelText("Access password");
    await user.type(password, "incorrect password");
    await user.click(screen.getByRole("button", { name: "Unlock" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Enter the Risk access password");

    await user.click(screen.getByRole("button", { name: "Unlock" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Too many login attempts");
    expect(fetchMock.mock.calls.every((call) => !String(call[0]).endsWith("/accounts"))).toBe(true);
  });

  it("enters the authenticated shell after login and logs out the current device", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/session")) return jsonResponse({ authenticated: false });
      if (url.endsWith("/auth/login")) return jsonResponse({ authenticated: true });
      if (url.endsWith("/auth/logout")) return new Response(null, { status: 204 });
      if (url.endsWith("/accounts")) return new Promise<Response>(() => undefined);
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<RiskPage />);

    await user.type(await screen.findByLabelText("Access password"), "correct shared password");
    await user.click(screen.getByRole("button", { name: "Unlock" }));
    const logout = await screen.findByRole("button", { name: "Sign out" });
    expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/accounts"))).toBe(true);

    await user.click(logout);
    expect((await screen.findByLabelText("Access password") as HTMLInputElement).value).toBe("");
    expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/auth/logout"))).toBe(true);
  });

  it("shows the configured default account immediately after login even when an older preference omitted it", async () => {
    window.localStorage.setItem("btc-options-risk-visible-accounts-v1", JSON.stringify(["income"]));
    const evaluateRequests: Record<string, unknown>[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/session")) return jsonResponse({ authenticated: false });
      if (url.endsWith("/auth/login")) return jsonResponse({ authenticated: true });
      if (url.endsWith("/accounts")) {
        return jsonResponse({
          defaultAccountId: "moonfund",
          accounts: [
            { id: "moonfund", label: "MoonFund", status: "configured" },
            { id: "income", label: "Income", status: "configured" }
          ]
        });
      }
      if (url.endsWith("/evaluate")) {
        evaluateRequests.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return new Promise<Response>(() => undefined);
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<RiskPage />);

    await user.type(await screen.findByLabelText("Access password"), "correct shared password");
    await user.click(screen.getByRole("button", { name: "Unlock" }));

    expect(await screen.findByRole("button", { name: "MoonFund" })).not.toBeNull();
    await waitFor(() => expect(evaluateRequests.at(-1)).toMatchObject({
      accountIds: ["moonfund", "income"]
    }));
    expect(window.localStorage.getItem("btc-options-risk-visible-accounts-v1")).toBe('["moonfund","income"]');
  });

  it("clears the authenticated view when an API request reports an ended session", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/session")) return jsonResponse({ authenticated: true });
      if (url.endsWith("/accounts")) {
        return jsonResponse({ error: { code: "authentication_required" } }, 401);
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<RiskPage />);

    expect((await screen.findByRole("alert")).textContent).toContain("Your Risk session ended");
    expect(screen.getByLabelText("Access password")).not.toBeNull();
    await waitFor(() => expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/evaluate"))).toBe(false));
  });
});
