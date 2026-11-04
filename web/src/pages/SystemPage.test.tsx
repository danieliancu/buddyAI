import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SystemInfo } from "../api";
import SystemPage from "./SystemPage";

const mocks = vi.hoisted(() => ({ info: vi.fn() }));
vi.mock("../api", async (orig) => {
  const real = await orig<typeof import("../api")>();
  return { ...real, api: { ...real.api, system: { ...real.api.system, info: mocks.info } } };
});
vi.mock("../components/SecurityCard", () => ({ default: () => null }));
vi.mock("../languages", () => ({ useLanguages: () => new Map() }));

const COMMIT = "713a832a2c6f5f0d1b9e4c3a7d8e6f5a4b3c2d1e";

function info(over: Partial<SystemInfo> = {}): SystemInfo {
  return {
    version: "0.1.0",
    commit: COMMIT,
    device_ws_url: "wss://api.example.com/ws/device",
    base_url: "https://api.example.com",
    mdns_enabled: false,
    mock_providers: false,
    ai_profile: "openai",
    search_backend: "ddgs_brave",
    search_legacy_categories: ["transport", "news"],
    deletion_ledger: { enabled: false, pending: 0, oldest_pending_s: 0 },
    keys: {},
    ...over,
  };
}

async function show(data: SystemInfo) {
  mocks.info.mockResolvedValue(data);
  render(
    <MemoryRouter>
      <SystemPage />
    </MemoryRouter>,
  );
  await screen.findByText(/olá server v0\.1\.0/);
}

describe("SystemPage", () => {
  beforeEach(() => mocks.info.mockReset());

  it("shows the version and the full commit id", async () => {
    await show(info());
    expect(screen.getByText(COMMIT)).toBeTruthy();
  });

  it("says when the commit is unknown", async () => {
    await show(info({ commit: "unknown" }));
    expect(screen.getByText("unknown")).toBeTruthy();
  });

  it("shows the search backend and its hosted categories", async () => {
    await show(info());
    expect(screen.getByText("ddgs_brave")).toBeTruthy();
    expect(screen.getByText("transport, news")).toBeTruthy();
  });

  it("does not list categories the active backend ignores", async () => {
    await show(info({ search_backend: "legacy_openai", search_legacy_categories: ["transport"] }));
    expect(screen.getByText("legacy_openai")).toBeTruthy();
    expect(screen.getByText("Not used by this backend")).toBeTruthy();
    expect(screen.queryByText("transport")).toBeNull();
  });
});
