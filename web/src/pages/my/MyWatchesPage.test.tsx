import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Account, Onboarding } from "../../api";
import { CustomerCtx } from "./session";
import MyWatchesPage from "./MyWatchesPage";

const me = vi.hoisted(() => ({
  onboarding: vi.fn(),
  usage: vi.fn(),
  devices: { list: vi.fn() },
}));
vi.mock("../../api", async (orig) => {
  const real = await orig<typeof import("../../api")>();
  return { ...real, api: { ...real.api, me: { ...real.api.me, ...me, devices: { ...real.api.me.devices, ...me.devices } } } };
});
vi.mock("../../live", () => ({ useLive: () => ({ connected: true }) }));

const account = { id: 7, email: "jane@example.com", name: "Jane", email_verified: true } as Account;

function order(extra: Partial<NonNullable<Onboarding["order"]>> = {}): Onboarding {
  return {
    eligible: true,
    order: { id: 42, status: "paid", created_at: "2026-11-10T10:00:00Z", carrier: "", tracking_number: "", ...extra },
    has_password: true,
    email_verified: true,
    watches: 0,
    care: null,
    complete: false,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <CustomerCtx.Provider value={{ account, setAccount: () => {}, reload: async () => {}, signOut: async () => {} }}>
        <MyWatchesPage />
      </CustomerCtx.Provider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  me.devices.list.mockResolvedValue([]);
  me.usage.mockResolvedValue({ period_start: "", reset_at: "", questions: 0 });
});

describe("My watches before the first watch is paired", () => {
  it("paid: order number, status, and pairing once the watch arrives", async () => {
    me.onboarding.mockResolvedValue(order());
    renderPage();
    const card = await screen.findByTestId("order-card");
    expect(card.textContent).toMatch(/Order #42/);
    expect(card.textContent).toMatch(/Paid — preparing/);
    expect(card.textContent).toMatch(/pair it as soon as it arrives/);
    expect(screen.queryByText("Track my parcel")).toBeNull();
    expect(document.body.textContent).toMatch(/Connect your watch to Wi-Fi\. When the six-digit code appears on your watch, enter it here\./);
    expect(document.body.textContent).not.toMatch(/Choose your phone/);
  });

  it("shipped with Royal Mail: Track my parcel opens Royal Mail tracking", async () => {
    me.onboarding.mockResolvedValue(order({ status: "shipped", carrier: "Royal Mail", tracking_number: "AB123456789GB" }));
    renderPage();
    const link = await screen.findByRole("link", { name: /Track my parcel/ });
    expect(link.getAttribute("href")).toBe("https://www.royalmail.com/track-your-item#/tracking-results/AB123456789GB");
    expect(screen.getByTestId("order-card").textContent).toMatch(/on its way/);
  });

  it("no card for a cancelled order", async () => {
    me.onboarding.mockResolvedValue(order({ status: "cancelled" }));
    renderPage();
    await screen.findByText("Set up your first watch");
    expect(screen.queryByTestId("order-card")).toBeNull();
  });
});
