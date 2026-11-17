import { describe, expect, it } from "vitest";
import { trackingUrl } from "./tracking";

describe("trackingUrl", () => {
  it("links Royal Mail parcels, whatever the spelling or spaces", () => {
    expect(trackingUrl("Royal Mail", "AB 1234 5678 GB")).toBe("https://www.royalmail.com/track-your-item#/tracking-results/AB12345678GB");
    expect(trackingUrl("royal  mail tracked 48", "AB123")).toContain("AB123");
    expect(trackingUrl("", "AB123")).toContain("royalmail.com");
  });

  it("gives no link without a number or for another carrier", () => {
    expect(trackingUrl("Royal Mail", "")).toBeNull();
    expect(trackingUrl("DPD", "123")).toBeNull();
  });
});
