import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Account, CareActivation, MyPlan, Onboarding } from "../../../api";
import { FakeWatch, type FakeWatchOptions } from "../../../ble/fakeWatch";
import { Provisioner } from "../../../ble/provisioner";
import { CustomerCtx } from "../session";
import SetupPage from "./SetupPage";

const me = vi.hoisted(() => ({
  onboarding: vi.fn(),
  setPlatform: vi.fn(),
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
    platform: null,
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
    usage: { used: 0, limit: 1000, remaining: 1000, used_pct: 0, included: 1000, extra: 0, period_start: "", reset_at: "" },
    thresholds: [80, 95, 100],
    prices: { currency: "GBP", care_price_pence: 790, topup_price_pence: 199, topup_interactions: 250 },
    can_subscribe: false,
    topup_available: false,
    can_manage_billing: true,
    care_activation: null,
    topups: [],
  };
}

function renderSetup(props: Parameters<typeof SetupPage>[0] = {}) {
  return render(
    <MemoryRouter>
      <CustomerCtx.Provider value={{ account, setAccount: () => {}, reload: async () => {}, signOut: async () => {} }}>
        <SetupPage {...props} />
      </CustomerCtx.Provider>
    </MemoryRouter>,
  );
}

const fakeConnect = (opts: FakeWatchOptions) => {
  const watch = new FakeWatch(opts);
  return { watch, connect: async () => new Provisioner(watch, { pollMs: 1 }) };
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  me.plan.mockResolvedValue(plan());
  me.setPlatform.mockImplementation(async (platform) => onboarding({ platform }));
});

describe("what the watch shows, then the phone", () => {
  it("first asks what the watch shows", async () => {
    me.onboarding.mockResolvedValue(onboarding());
    renderSetup();
    expect(await screen.findByRole("heading", { name: "What does your watch show?" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /A 6-digit code/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Wi-Fi setup/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Something else, or not sure?" }));
    expect(screen.getByTestId("watch-shows-help").textContent).toMatch(/Settings on the watch/);
  });

  it("a 6-digit code: straight to pairing, no Wi-Fi step", async () => {
    me.onboarding.mockResolvedValue(onboarding());
    renderSetup();
    fireEvent.click(await screen.findByRole("button", { name: /A 6-digit code/ }));
    expect(screen.getByLabelText("Code shown on the watch")).toBeTruthy();
    expect(screen.queryByText("1. Connect the watch to your Wi-Fi")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Which phone are you using?" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /My watch shows Wi-Fi setup/ })); // one tap to switch
    expect(await screen.findByRole("heading", { name: "Which phone are you using?" })).toBeTruthy();
  });

  it("a watch this account just removed is announced and goes straight to the code", async () => {
    me.onboarding.mockResolvedValue(onboarding({ waiting_watch: { name: "Gran's ola", expires_in_s: 250 } }));
    renderSetup();
    expect((await screen.findByTestId("waiting-watch")).textContent).toMatch(/Gran's ola” is online/);
    expect(screen.getByLabelText("Code shown on the watch")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "What does your watch show?" })).toBeNull();
  });

  async function chooseWifiPath() {
    fireEvent.click(await screen.findByRole("button", { name: /Wi-Fi setup/ }));
  }

  it("Wi-Fi setup: asks which phone, with Android and iPhone choices", async () => {
    me.onboarding.mockResolvedValue(onboarding());
    renderSetup();
    await chooseWifiPath();
    expect(await screen.findByRole("heading", { name: "Which phone are you using?" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Android/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /iPhone/ })).toBeTruthy();
  });

  it("Android: Bluetooth first, the ola-XXXX network stays hidden until help is needed", async () => {
    me.onboarding.mockResolvedValue(onboarding());
    renderSetup({ bleSupport: "supported" });
    await chooseWifiPath();
    fireEvent.click(await screen.findByRole("button", { name: /Android/ }));
    expect(me.setPlatform).toHaveBeenCalledWith("android");
    expect(await screen.findByRole("button", { name: "Connect to watch" })).toBeTruthy();
    expect(screen.queryByTestId("recovery")).toBeNull();
    expect(screen.getByText(/2.4 GHz/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Need help?" })); // asked for help
    expect(screen.getByTestId("recovery")).toBeTruthy();
  });

  it("iPhone: only the ola-XXXX steps, with the password and QR code from the watch, no Bluetooth", async () => {
    me.onboarding.mockResolvedValue(onboarding());
    renderSetup({ bleSupport: "ios" });
    await chooseWifiPath();
    fireEvent.click(await screen.findByRole("button", { name: /iPhone/ }));
    const steps = await screen.findByTestId("softap-steps");
    expect(within(steps).getAllByText(/ola-XXXX/).length).toBeGreaterThan(0);
    expect(steps.textContent).toMatch(/password shown on the watch/);
    expect(steps.textContent).toMatch(/QR code/);
    expect(steps.textContent).toMatch(/2\.4 GHz/);
    expect(screen.queryByRole("button", { name: "Connect to watch" })).toBeNull();
  });

  it("resumes with the saved phone and lets the customer change it", async () => {
    me.onboarding.mockResolvedValue(onboarding({ platform: "iphone" }));
    renderSetup();
    expect(await screen.findByTestId("softap-steps")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Change phone" }));
    await waitFor(() => expect(me.setPlatform).toHaveBeenCalledWith(null));
    expect(await screen.findByRole("heading", { name: "Which phone are you using?" })).toBeTruthy();
  });
});

describe("Android Bluetooth setup", () => {
  const android = () => me.onboarding.mockResolvedValue(onboarding({ platform: "android" }));

  it("unsupported browser: explains and shows the recovery steps", async () => {
    android();
    renderSetup({ bleSupport: "unsupported" });
    expect(await screen.findByTestId("ble-unsupported")).toBeTruthy();
    expect(screen.getByTestId("recovery")).toBeTruthy();
  });

  it("not on https: explains", async () => {
    android();
    renderSetup({ bleSupport: "insecure" });
    expect((await screen.findByTestId("ble-unsupported")).textContent).toMatch(/https/);
  });

  it("permission denied", async () => {
    android();
    renderSetup({ bleSupport: "supported", connectWatch: async () => Promise.reject(new DOMException("denied", "NotAllowedError")) });
    fireEvent.click(await screen.findByRole("button", { name: "Connect to watch" }));
    expect(await screen.findByTestId("ble-problem-denied")).toBeTruthy();
    expect(screen.getByTestId("recovery")).toBeTruthy();
  });

  it("chooser cancelled / no watch found", async () => {
    android();
    renderSetup({ bleSupport: "supported", connectWatch: async () => Promise.reject(new DOMException("cancelled", "NotFoundError")) });
    fireEvent.click(await screen.findByRole("button", { name: "Connect to watch" }));
    expect(await screen.findByTestId("ble-problem-cancelled")).toBeTruthy();
  });

  it("connection timeout", async () => {
    android();
    renderSetup({ bleSupport: "supported", connectWatch: async () => Promise.reject(new DOMException("slow", "TimeoutError")) });
    fireEvent.click(await screen.findByRole("button", { name: "Connect to watch" }));
    expect(await screen.findByTestId("ble-problem-timeout")).toBeTruthy();
  });

  async function connectAndUnlock(password = "K7P4M9XQ") {
    fireEvent.click(await screen.findByRole("button", { name: "Connect to watch" }));
    const input = await screen.findByLabelText("Setup password shown on the watch");
    fireEvent.change(input, { target: { value: password } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  }

  it("success: setup password, network, Wi-Fi password, joined; then pairing", async () => {
    android();
    const { watch, connect } = fakeConnect({ password: "K7P4M9XQ", networks: [{ s: "Home", r: -50, a: 3 }] });
    renderSetup({ bleSupport: "supported", connectWatch: connect });
    await connectAndUnlock("k7p4 m9xq"); // typed as read from the watch
    const pass = await screen.findByLabelText("Wi-Fi password");
    fireEvent.change(pass, { target: { value: "correct horse" } });
    fireEvent.click(screen.getByRole("button", { name: "Send to watch" }));
    expect(await screen.findByTestId("ble-success")).toBeTruthy();
    expect(watch.saved).toEqual({ ssid: "Home", pass: "correct horse" });
    expect(screen.queryByTestId("recovery")).toBeNull();
    expect(await screen.findByRole("button", { name: "Set up Wi-Fi again" })).toBeTruthy(); // step 1 done
    expect(screen.getByLabelText("Code shown on the watch")).toBeTruthy();
    expect(localStorage.getItem("ola.setup.wifiDone.7")).toBe("1");
  });

  it("wrong setup password is explained and can be corrected", async () => {
    android();
    const { connect } = fakeConnect({ password: "K7P4M9XQ" });
    renderSetup({ bleSupport: "supported", connectWatch: connect });
    await connectAndUnlock("AAAAAAAA");
    expect(await screen.findByText(/does not match/)).toBeTruthy();
  });

  it("wrong Wi-Fi password: failure state, recovery offered, retry goes back to the network form", async () => {
    android();
    const { connect } = fakeConnect({ password: "K7P4M9XQ", networks: [{ s: "Home", r: -50, a: 3 }], join: () => "wrong_password" });
    renderSetup({ bleSupport: "supported", connectWatch: connect });
    await connectAndUnlock();
    fireEvent.change(await screen.findByLabelText("Wi-Fi password"), { target: { value: "not-it-123" } });
    fireEvent.click(screen.getByRole("button", { name: "Send to watch" }));
    expect(await screen.findByTestId("ble-problem-wrong_wifi_password")).toBeTruthy();
    expect(screen.getByTestId("recovery")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Wi-Fi password")).toBeTruthy();
  });

  it("network not found mentions 2.4 GHz", async () => {
    android();
    const { connect } = fakeConnect({ password: "K7P4M9XQ", networks: [{ s: "Home-5G", r: -50, a: 3 }], join: () => "not_found" });
    renderSetup({ bleSupport: "supported", connectWatch: connect });
    await connectAndUnlock();
    fireEvent.change(await screen.findByLabelText("Wi-Fi password"), { target: { value: "whatever123" } });
    fireEvent.click(screen.getByRole("button", { name: "Send to watch" }));
    expect((await screen.findByTestId("ble-problem-wifi_not_found")).textContent).toMatch(/2.4 GHz/);
  });

  it("watch disconnected mid-setup", async () => {
    android();
    const { watch, connect } = fakeConnect({ password: "K7P4M9XQ", networks: [{ s: "Home", r: -50, a: 3 }] });
    renderSetup({ bleSupport: "supported", connectWatch: connect });
    await connectAndUnlock();
    await screen.findByLabelText("Wi-Fi password");
    act(() => watch.drop());
    expect(await screen.findByTestId("ble-problem-disconnected")).toBeTruthy();
    expect(screen.getByTestId("recovery")).toBeTruthy();
  });

  it("rejects an invalid Wi-Fi password before sending", async () => {
    android();
    const { watch, connect } = fakeConnect({ password: "K7P4M9XQ", networks: [{ s: "Home", r: -50, a: 3 }] });
    renderSetup({ bleSupport: "supported", connectWatch: connect });
    await connectAndUnlock();
    fireEvent.change(await screen.findByLabelText("Wi-Fi password"), { target: { value: "short" } });
    fireEvent.click(screen.getByRole("button", { name: "Send to watch" }));
    expect(await screen.findByText(/at least 8 characters/)).toBeTruthy();
    expect(watch.saved).toBeNull();
  });
});

describe("order, account and ola Care states", () => {
  it("no paid watch order: no setup", async () => {
    me.onboarding.mockResolvedValue(onboarding({ eligible: false, order: null }));
    renderSetup();
    expect(await screen.findByTestId("no-order")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Which phone are you using?" })).toBeNull();
  });

  it("payment still processing: no setup yet", async () => {
    me.onboarding.mockResolvedValue(onboarding({ order: { id: 1, status: "payment_pending", created_at: "" } }));
    renderSetup();
    expect(await screen.findByText(/still confirming the payment/)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Which phone are you using?" })).toBeNull();
  });

  it("email not confirmed: pairing waits", async () => {
    me.onboarding.mockResolvedValue(onboarding({ platform: "iphone", email_verified: false }));
    localStorage.setItem("ola.setup.wifiDone.7", "1");
    renderSetup();
    expect(await screen.findByText(/Confirm your email address first/)).toBeTruthy();
    expect(screen.queryByLabelText("Code shown on the watch")).toBeNull();
  });

  it("before pairing: the trial starts with the watch, not before", async () => {
    me.onboarding.mockResolvedValue(onboarding({ platform: "iphone" }));
    renderSetup();
    expect((await screen.findByTestId("care-pending-pairing")).textContent).toMatch(/starts when the watch is paired.*£7\.90 a month/s);
  });

  it("paired and trial running: end date, price and how billing continues", async () => {
    me.onboarding.mockResolvedValue(onboarding({ watches: 1, care: care("active"), complete: true }));
    me.plan.mockResolvedValue(plan("trial", { trial_end: "2027-01-05T10:00:00Z" }));
    renderSetup();
    const t = await screen.findByTestId("care-trial-active");
    expect(t.textContent).toMatch(/5 Jan 2027/);
    expect(t.textContent).toMatch(/£7\.90 a month/);
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

it("the pairing code can be typed even if the Wi-Fi step wasn't confirmed (watch already online)", async () => {
  me.onboarding.mockResolvedValue(onboarding({ platform: "iphone" }));
  renderSetup();
  expect(await screen.findByLabelText("Code shown on the watch")).toBeTruthy();
});

it("Add watch (?another=1) runs the setup again even when the account already has a watch", async () => {
  me.onboarding.mockResolvedValue(onboarding({ watches: 1, care: care("active"), complete: true, platform: "iphone" }));
  render(
    <MemoryRouter initialEntries={["/my/setup?another=1"]}>
      <CustomerCtx.Provider value={{ account, setAccount: () => {}, reload: async () => {}, signOut: async () => {} }}>
        <SetupPage />
      </CustomerCtx.Provider>
    </MemoryRouter>,
  );
  expect(await screen.findByTestId("softap-steps")).toBeTruthy(); // not the "already paired" screen
  expect(screen.getByLabelText("Code shown on the watch")).toBeTruthy();
});
