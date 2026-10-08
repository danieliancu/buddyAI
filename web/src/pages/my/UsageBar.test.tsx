import { describe, expect, it } from "vitest";
import { usagePct } from "./MyWatchesPage";

describe("My watches: monthly AI interactions bar", () => {
  it("is the % used of the allowance (1,000 -> 100)", () => {
    expect(usagePct({ period_start: "", reset_at: "", questions: 15, limit: 1000 })).toBe(1);
    expect(usagePct({ period_start: "", reset_at: "", questions: 15, limit: 1000, used_pct: 1 })).toBe(1);
    expect(usagePct({ period_start: "", reset_at: "", questions: 240, limit: 1000 })).toBe(24);
    expect(usagePct({ period_start: "", reset_at: "", questions: 1300, limit: 1000 })).toBe(100);
    expect(usagePct({ period_start: "", reset_at: "", questions: 3 })).toBe(0); // no limit known
  });
});
