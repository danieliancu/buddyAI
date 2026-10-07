import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import type { MyPlan } from "../../api";
import { CareDetails } from "./PlanCard";

const me = vi.hoisted(() => ({ billingPortal: vi.fn(), activateCare: vi.fn(), subscribe: vi.fn() }));
vi.mock("../../api", async (orig) => {
  const real = await orig<typeof import("../../api")>();
  return { ...real, api: { ...real.api, me: { ...real.api.me, ...me } } };
});

const plan = (status: MyPlan["status"]): MyPlan =>
  ({
    status,
    care_activation: null,
    prices: { currency: "GBP", care_price_pence: 799, topup_price_pence: 199, topup_adds_pct: 26 },
  }) as MyPlan;

describe("ola Care cancelled", () => {
  it("says clearly what still works, that nothing is charged, and offers to keep it", async () => {
    me.billingPortal.mockResolvedValue({ url: "about:blank" });
    const assign = vi.fn();
    Object.defineProperty(window, "location", { value: { ...window.location, assign }, writable: true });
    render(
      <MemoryRouter>
        <CareDetails plan={plan({ kind: "trial", trial_end: "2027-01-05T00:00:00Z", cancel_at_period_end: true })} onChange={() => {}} />
      </MemoryRouter>,
    );
    const box = screen.getByTestId("care-cancelled");
    expect(box.textContent).toMatch(/ola Care is cancelled/);
    expect(box.textContent).toMatch(/still works until 5 Jan 2027/);
    expect(box.textContent).toMatch(/Nothing more will be charged/);
    expect(box.textContent).not.toMatch(/charged automatically/);
    fireEvent.click(screen.getByRole("button", { name: "Keep ola Care" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("about:blank"));
  });

  it("a running trial still explains the first charge", () => {
    render(
      <MemoryRouter>
        <CareDetails plan={plan({ kind: "trial", trial_end: "2027-01-05T00:00:00Z" })} onChange={() => {}} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("care-trial").textContent).toMatch(/Then £7\.99 a month, charged automatically/);
  });
});

describe("after the free trial", () => {
  it("paid plan: the next payment, its amount and date", () => {
    render(
      <MemoryRouter>
        <CareDetails plan={plan({ kind: "active", period_end: "2027-02-05T00:00:00Z" })} onChange={() => {}} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("care-next-payment").textContent).toMatch(/Next payment: £7\.99 on 5 Feb 2027/);
  });

  it("plan ended: why the watch is quiet, data kept, subscribe again charged today without a trial", async () => {
    me.subscribe.mockResolvedValue({ url: "about:blank#subscribe" });
    const assign = vi.fn();
    Object.defineProperty(window, "location", { value: { ...window.location, assign }, writable: true });
    render(
      <MemoryRouter>
        <CareDetails
          plan={{ ...plan({ kind: "canceled", period_end: "2027-01-05T00:00:00Z" }), can_subscribe: true }}
          onChange={() => {}}
        />
      </MemoryRouter>,
    );
    const box = screen.getByTestId("care-ended");
    expect(box.textContent).toMatch(/ola Care ended on 5 Jan 2027/);
    expect(box.textContent).toMatch(/doesn't answer until you subscribe again/);
    expect(box.textContent).toMatch(/notes and reminders are kept/);
    expect(box.textContent).toMatch(/Charged today.*No new free trial/);
    fireEvent.click(screen.getByRole("button", { name: /Subscribe again — £7\.99 \/ month/ }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("about:blank#subscribe"));
  });
});
