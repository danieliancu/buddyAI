import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Order } from "../api";
import OrdersPage from "./OrdersPage";

const mocks = vi.hoisted(() => ({ list: vi.fn(), update: vi.fn(), resendShipped: vi.fn() }));
vi.mock("../api", async (orig) => {
  const real = await orig<typeof import("../api")>();
  return { ...real, api: { ...real.api, orders: mocks } };
});

function order(over: Partial<Order> = {}): Order {
  return {
    id: 5,
    stripe_session_id: "cs_1",
    stripe_payment_intent: "pi_1",
    account_id: 9,
    email: "jane@example.com",
    currency: "gbp",
    amount_total: 11900,
    amount_tax: 0,
    amount_shipping: 0,
    amount_discount: 0,
    discount_code: "",
    amount_refunded: 0,
    status: "shipped",
    shipping_name: "Jane",
    shipping_address: {},
    country: "GB",
    carrier: "Royal Mail",
    tracking_number: "AB1GB",
    created_at: "2026-11-10T10:00:00Z",
    shipped_at: "2026-11-11T10:00:00Z",
    delivered_at: null,
    ship_email: null,
    ...over,
  };
}

function show() {
  render(
    <MemoryRouter>
      <OrdersPage />
    </MemoryRouter>,
  );
}

describe("OrdersPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("a dispatch email that failed: shown, and Resend does not touch the order", async () => {
    mocks.list.mockResolvedValue([order({ ship_email: { state: "failed", at: "2026-11-11T10:00:00Z", error: "OSError", attempts: 11 } })]);
    mocks.resendShipped.mockResolvedValue(order({ ship_email: { state: "sent", at: "2026-11-12T10:00:00Z", error: "", attempts: 1 } }));
    show();
    expect((await screen.findByTestId("ship-email-5")).textContent).toMatch(/Email failed/);
    fireEvent.click(screen.getByRole("button", { name: /Resend/ }));
    await waitFor(() => expect(screen.getByTestId("ship-email-5").textContent).toMatch(/Email sent/));
    expect(mocks.resendShipped).toHaveBeenCalledWith(5);
    expect(mocks.update).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /Resend/ })).toBeNull();
  });

  it("shows the amount Stripe actually refunded, also when partial", async () => {
    mocks.list.mockResolvedValue([order({ status: "partial_refund", amount_refunded: 2000 })]);
    show();
    expect((await screen.findByTestId("refunded-5")).textContent).toMatch(/£20\.00 refunded of £119\.00/);
  });

  it("cancelling explains that the refund is made separately in Stripe", async () => {
    mocks.list.mockResolvedValue([order({ status: "paid", tracking_number: "", carrier: "" })]);
    show();
    fireEvent.click(await screen.findByRole("button", { name: /Mark shipped/ }));
    expect(screen.queryByTestId("cancel-refund-note")).toBeNull();
    fireEvent.change(document.getElementById("o-status")!, { target: { value: "cancelled" } });
    expect(screen.getByTestId("cancel-refund-note").textContent).toMatch(/does not refund.*Stripe dashboard/s);
    expect(screen.getByRole("button", { name: "Cancel order" })).toBeTruthy();
  });
});
