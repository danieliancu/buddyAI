import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { MyPlan } from "../api";
import { fmtLongDate, MonthlyUsage, turnRefusedText } from "./BillingBits";

const usage = (used: number): MyPlan["usage"] => ({
  used,
  unlimited: true,
  period_start: "2026-10-08T00:00:00Z",
  reset_at: "2026-11-08T00:00:00Z",
});

describe("AI interactions (My Account → olá Care)", () => {
  it("says unlimited and shows only how many were used - no bar, no warnings, no allowance", () => {
    render(<MonthlyUsage usage={usage(2400)} />);
    const box = screen.getByTestId("monthly-usage").textContent ?? "";
    expect(box).toMatch(/Unlimited/);
    expect(box).toMatch(/2,400 interactions this month/);
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(box).not.toMatch(/remaining|of 1,000|renews|%|£|cost|token/i);
  });

  it("one interaction is singular; long dates are British English", () => {
    render(<MonthlyUsage usage={usage(1)} />);
    expect(screen.getByTestId("monthly-usage").textContent).toMatch(/1 interaction this month/);
    expect(fmtLongDate("2027-01-03T10:00:00Z")).toBe("3 January 2027");
  });

  it("no refusal is explained as a used-up allowance", () => {
    expect(turnRefusedText("limit_reached")).not.toMatch(/monthly AI interactions/);
  });
});
