import { describe, expect, it } from "vitest";
import { sessionIdFrom, viewFor } from "./checkoutStatus";
import { canBuyIn, checkoutBody, parseShopStatus } from "./shop";

describe("thank-you page: only what Stripe confirmed", () => {
  it("never claims success before the payment is confirmed", () => {
    const first = viewFor(null);
    expect(first.title).toMatch(/Confirming/);
    expect(first.showNext).toBe(false);
    expect(first.poll).toBe(true);
    const processing = viewFor({ state: "processing", needs_password: false });
    expect(processing.showNext).toBe(false);
    expect(processing.title).not.toMatch(/confirmed|thank/i);
  });

  it("paid: next steps, and the password email when the account is new", () => {
    const v = viewFor({ state: "paid", needs_password: true });
    expect(v.title).toMatch(/Payment confirmed/);
    expect(v.showNext).toBe(true);
    expect(v.poll).toBe(false);
    expect(v.body).toMatch(/set your olá account password/);
    expect(viewFor({ state: "paid", needs_password: false }).body).toMatch(/Sign in/);
  });

  it("slow payment methods: stop polling and promise an email", () => {
    const v = viewFor({ state: "processing", needs_password: false }, { timedOut: true });
    expect(v.poll).toBe(false);
    expect(v.body).toMatch(/email you/);
    expect(v.showNext).toBe(false);
  });

  it("failed and cancelled payments", () => {
    expect(viewFor({ state: "failed", needs_password: false }).tone).toBe("error");
    expect(viewFor({ state: "failed", needs_password: false }).body).toMatch(/Nothing was charged/);
    expect(viewFor({ state: "cancelled", needs_password: false }).showNext).toBe(false);
  });

  it("only accepts real checkout session ids", () => {
    expect(sessionIdFrom("?session_id=cs_test_a1B2c3D4e5")).toBe("cs_test_a1B2c3D4e5");
    expect(sessionIdFrom("?session_id=<script>")).toBeNull();
    expect(sessionIdFrom("")).toBeNull();
    expect(viewFor(null, { missingSession: true }).poll).toBe(false);
  });
});

describe("buy box: the olá Care terms must be accepted", () => {
  const status = parseShopStatus({
    open: true,
    currencies: ["gbp", "eur"],
    trial_days: 30,
    care_terms: { gbp: { version: "care-2026-12", sha256: "ab".repeat(32), text: "I agree … £7.90 per month …", amount_minor: 790, interval: "month" } },
  });

  it("a currency without published terms can't be bought", () => {
    expect(canBuyIn(status, "GBP")).toBe(true);
    expect(canBuyIn(status, "EUR")).toBe(false);
    expect(canBuyIn(parseShopStatus({ open: false, currencies: ["gbp"] }), "GBP")).toBe(false);
  });

  it("checkout sends the exact version and hash of the terms shown", () => {
    expect(checkoutBody("GBP", status.careTerms!.gbp, true)).toEqual({
      currency: "gbp",
      care_terms_accepted: true,
      care_terms_version: "care-2026-12",
      care_terms_sha256: "ab".repeat(32),
    });
  });
});

import { parseBadge } from "./account";

describe("header: signed-in badge", () => {
  it("first name only when signed in, otherwise Sign in stays", () => {
    expect(parseBadge({ signed_in: true, name: "Jane", last_name: "Buyer" })).toEqual({ signedIn: true, name: "Jane", lastName: "Buyer" });
    expect(parseBadge({ signed_in: false })).toEqual({ signedIn: false, name: "", lastName: "" });
    expect(parseBadge({ signed_in: true, name: "" }).signedIn).toBe(false);
    expect(parseBadge(null).signedIn).toBe(false);
    expect(parseBadge({ signed_in: true, name: "x".repeat(40) }).name).toHaveLength(24);
  });
});
