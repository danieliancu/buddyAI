import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Account, CareActivation, MyPlan, Onboarding } from "../../../api";
import { CustomerCtx } from "../session";
import SetupPage from "./SetupPage";

const me = vi.hoisted(() => ({
  onboarding: vi.fn(),
  plan: vi.fn(),
  activateCare: vi.fn(),
  devices: { pair: vi.fn() },
}));
vi.mock("../../../api", async (orig) => {
  const real = await orig<typeof import("../../../api")>();
  return { ...real, api: { ...real.api, me: { ...real.api.me, ...me } } };
});

const account: Account = {
  id: 7,
  email: "jane@example.com",
  name: "Jane",
  country: "GB",
  email_verified: true,
  status: "active",
  has_password: true,
  created_at: "2026-10-01T10:00:00Z",
} as Account;

function care(status: CareActivation["status"], extra: Partial<CareActivation> = {}): CareActivation {
  return { status, reason: null, error: null, can_retry: status === "failed", trial_days: 30, currency: "gbp", activated_at: null, ...extra };
}

function onboarding(extra: Partial<Onboarding> = {}): Onboarding {
  return {
    eligible: true,
    order: { id: 12, status: "paid", created_at: "2026-10-01T10:00:00Z" },
    has_password: true,
    email_verified: true,
    watches: 0,
    care: care("awaiting_pairing"),
    complete: false,
    ...extra,
  };
}

function plan(kind: MyPlan["status"]["kind"] = "none", extra: Partial<MyPlan["status"]> = {}): MyPlan {
  return {
    billing_enabled: true,
    enforced: true,
    status: { kind, ...extra },
    usage: { used: 0, unlimited: true, period_start: "", reset_at: "" },
    prices: { currency: "GBP", care_price_pence: 799 },
    can_subscribe: false,
    can_manage_billing: true,
    care_activation: null,
    topups: [],
  };
}

function renderSetup() {
  return render(
    <MemoryRouter>
      <CustomerCtx.Provider value={{ account, setAccount: () => {}, reload: async () => {}, signOut: async () => {} }}>
        <SetupPage />
      </CustomerCtx.Provider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  me.plan.mockResolvedValue(plan());
});

describe("Wi-Fi on the watch, then the code", () => {
  it("shows the three steps and goes straight to the code: no phone choice, no Wi-Fi form", async () => {
    me.onboarding.mockResolvedValue(onboarding());
    renderSetup();
    const steps = await screen.findByTestId("setup-steps");
    expect(steps.textContent).toMatch(/Choose your Wi-Fi network and type its password/);
    expect(steps.textContent).toMatch(/6-digit code/);
    expect(screen.getByLabelText("Code shown on the watch")).toBeTruthy();
    expect(screen.getByText(/valid for 5 minutes/)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/Bluetooth|iPhone|Android|QR|ola-XXXX/);
  });

  it("help when the watch shows no code", async () => {
    me.onboarding.mockResolvedValue(onboarding());
    renderSetup();
    fireEvent.click(await screen.findByRole("button", { name: "No code on the watch?" }));
    const help = screen.getByTestId("setup-help");
    expect(help.textContent).toMatch(/2\.4 GHz/);
    expect(help.textContent).toMatch(/Settings → Wi-Fi setup/);
    expect(help.textContent).toMatch(/8 seconds/);
  });

  it("a watch this account just removed is announced and goes straight to the code", async () => {
    me.onboarding.mockResolvedValue(onboarding({ waiting_watch: { name: "Gran's olá", expires_in_s: 250 } }));
    renderSetup();
    expect((await screen.findByTestId("waiting-watch")).textContent).toMatch(/Gran's olá” is online/);
    expect(screen.getByLabelText("Code shown on the watch")).toBeTruthy();
    expect(screen.queryByTestId("setup-steps")).toBeNull();
  });
});

describe("order, account and olá Care states", () => {
  it("no paid watch order: no setup", async () => {
    me.onboarding.mockResolvedValue(onboarding({ eligible: false, order: null }));
    renderSetup();
    expect(await screen.findByTestId("no-order")).toBeTruthy();
    expect(screen.queryByLabelText("Code shown on the watch")).toBeNull();
  });

  it("payment still processing: no setup yet", async () => {
    me.onboarding.mockResolvedValue(onboarding({ order: { id: 1, status: "payment_pending", created_at: "" } }));
    renderSetup();
    expect(await screen.findByText(/still confirming the payment/)).toBeTruthy();
    expect(screen.queryByLabelText("Code shown on the watch")).toBeNull();
  });

  it("email not confirmed: pairing waits", async () => {
    me.onboarding.mockResolvedValue(onboarding({ email_verified: false }));
    renderSetup();
    expect(await screen.findByText(/Confirm your email address first/)).toBeTruthy();
    expect(screen.queryByLabelText("Code shown on the watch")).toBeNull();
  });

  it("before pairing: the trial starts with the watch, not before", async () => {
    me.onboarding.mockResolvedValue(onboarding());
    renderSetup();
    expect((await screen.findByTestId("care-pending-pairing")).textContent).toMatch(/starts when the watch is paired.*£7\.99 a month/s);
  });

  it("paired and trial running: end date, price and how billing continues", async () => {
    me.onboarding.mockResolvedValue(onboarding({ watches: 1, care: care("active"), complete: true }));
    me.plan.mockResolvedValue(plan("trial", { trial_end: "2027-01-05T10:00:00Z" }));
    renderSetup();
    const t = await screen.findByTestId("care-trial-active");
    expect(t.textContent).toMatch(/5 Jan 2027/);
    expect(t.textContent).toMatch(/£7\.99 a month/);
    expect(t.textContent).toMatch(/until you cancel/);
  });

  it("paired but the subscription failed: pending state and a safe retry", async () => {
    me.onboarding.mockResolvedValue(onboarding({ watches: 1, care: care("failed", { error: "stripe_error" }) }));
    me.activateCare.mockResolvedValue({ care_activation: care("active") });
    renderSetup();
    expect((await screen.findByTestId("care-setup-pending")).textContent).toMatch(/Subscription setup pending/);
    expect(screen.queryByText(/is active/)).toBeNull(); // never claims an active plan
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(me.activateCare).toHaveBeenCalledTimes(1));
  });
});

it("Add watch (?another=1) runs the setup again even when the account already has a watch", async () => {
  me.onboarding.mockResolvedValue(onboarding({ watches: 1, care: care("active"), complete: true }));
  render(
    <MemoryRouter initialEntries={["/my/setup?another=1"]}>
      <CustomerCtx.Provider value={{ account, setAccount: () => {}, reload: async () => {}, signOut: async () => {} }}>
        <SetupPage />
      </CustomerCtx.Provider>
    </MemoryRouter>,
  );
  expect(await screen.findByTestId("setup-steps")).toBeTruthy(); // not the "already paired" screen
  expect(screen.getByLabelText("Code shown on the watch")).toBeTruthy();
});
