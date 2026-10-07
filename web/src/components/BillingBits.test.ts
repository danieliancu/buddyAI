import { describe, expect, it } from "vitest";
import type { CareActivation, MyPlan } from "../api";
import { careErrorText, carePlanStatus } from "./BillingBits";

const base = (status: MyPlan["status"], care: CareActivation | null = null): MyPlan =>
  ({
    status,
    care_activation: care,
    prices: { currency: "GBP", care_price_pence: 790, topup_price_pence: 199, topup_adds_pct: 26 },
  }) as MyPlan;
const care = (status: CareActivation["status"], error: string | null = null): CareActivation => ({
  status,
  error,
  reason: null,
  can_retry: status === "failed",
  trial_days: 90,
  currency: "gbp",
  activated_at: null,
});

describe("ola Care status shown to the customer", () => {
  it("before pairing, while starting, and after a failure (never 'active')", () => {
    expect(carePlanStatus(base({ kind: "none" }, care("awaiting_pairing"))).text).toBe("Free 90-day trial — starts when you pair your watch");
    expect(carePlanStatus(base({ kind: "none" }, care("activating"))).text).toMatch(/Starting/);
    const failed = carePlanStatus(base({ kind: "none" }, care("failed", "card_error")));
    expect(failed.text).toBe("Subscription setup pending");
    expect(failed.problem).toBe(true);
    expect(careErrorText("card_error")).toMatch(/update your card/i);
  });

  it("Stripe's confirmed status wins once the subscription exists", () => {
    expect(carePlanStatus(base({ kind: "trial", trial_end: "2027-01-05T00:00:00Z" }, care("active"))).text).toMatch(/Free trial until 5 Jan/);
    expect(carePlanStatus(base({ kind: "past_due" }, care("active"))).problem).toBe(true);
  });

  it("legacy accounts without a pending trial", () => {
    expect(carePlanStatus(base({ kind: "none" })).text).toBe("No plan yet");
  });
});
