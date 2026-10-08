import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { MyPlan } from "../api";
import { fmtLongDate, MonthlyUsage, turnRefusedText } from "./BillingBits";

const usage = (used: number, limit = 1000, extra = 0): MyPlan["usage"] => ({
  used,
  limit,
  remaining: Math.max(0, limit - used),
  used_pct: Math.min(100, Math.floor((used * 100) / limit)),
  included: limit - extra,
  extra,
  period_start: "2026-10-08T00:00:00Z",
  reset_at: "2026-11-08T00:00:00Z",
});

describe("Monthly usage (My Account → ola Care)", () => {
  it("shows %, used of allowance, remaining and the renewal date", () => {
    render(<MonthlyUsage usage={usage(240)} />);
    const box = screen.getByTestId("monthly-usage");
    expect(box.textContent).toMatch(/Monthly usage/);
    expect(box.textContent).toMatch(/24% used/);
    expect(box.textContent).toMatch(/240 of 1,000 interactions/);
    expect(box.textContent).toMatch(/760 interactions remaining/);
    expect(box.textContent).toMatch(/Renews on 8 November 2026/);
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("24");
    expect(screen.queryByTestId("usage-hint")).toBeNull(); // nothing to warn about yet
    expect(box.textContent).not.toMatch(/£|cost|token/i); // never internal costs
  });

  it("warns subtly at 80 %, more visibly at 95 %, and explains 100 %", () => {
    const { unmount } = render(<MonthlyUsage usage={usage(800)} />);
    expect(screen.getByTestId("usage-hint").textContent).toBe("You've used 80% of your monthly AI interactions.");
    unmount();
    const second = render(<MonthlyUsage usage={usage(960)} />);
    expect(screen.getByTestId("usage-hint").textContent).toMatch(/96% .* 40 left until 8 November 2026/);
    second.unmount();
    render(<MonthlyUsage usage={usage(1000)} />);
    const hint = screen.getByTestId("usage-hint").textContent ?? "";
    expect(hint).toMatch(/You've reached your 1,000 monthly AI interactions\. Your allowance renews on 8 November 2026/);
    expect(screen.getByTestId("monthly-usage").textContent).toMatch(/0 interactions remaining/);
  });

  it("counts extra interactions in the allowance", () => {
    render(<MonthlyUsage usage={usage(1100, 1250, 250)} />);
    const box = screen.getByTestId("monthly-usage").textContent ?? "";
    expect(box).toMatch(/1,100 of 1,250 interactions/);
    expect(box).toMatch(/150 interactions remaining/);
    expect(box).toMatch(/includes 250 extra this period/);
  });

  it("one remaining is singular; long dates are British English", () => {
    render(<MonthlyUsage usage={usage(999)} />);
    expect(screen.getByTestId("monthly-usage").textContent).toMatch(/1 interaction remaining/);
    expect(fmtLongDate("2027-01-03T10:00:00Z")).toBe("3 January 2027");
  });

  it("explains a refusal at the limit in interactions", () => {
    expect(turnRefusedText("limit_reached")).toMatch(/monthly AI interactions/);
    expect(turnRefusedText("limit_reached")).not.toMatch(/usage is used up/);
  });
});
