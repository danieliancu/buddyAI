import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import type { MyPlan } from "../../api";
import { CareDetails } from "./PlanCard";

const me = vi.hoisted(() => ({ billingPortal: vi.fn(), activateCare: vi.fn() }));
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
