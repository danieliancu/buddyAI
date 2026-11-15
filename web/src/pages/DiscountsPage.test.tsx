import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DiscountCode } from "../api";
import DiscountsPage from "./DiscountsPage";

const mocks = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), setActive: vi.fn() }));
vi.mock("../api", async (orig) => {
  const real = await orig<typeof import("../api")>();
  return { ...real, api: { ...real.api, discounts: mocks } };
});

function code(over: Partial<DiscountCode> = {}): DiscountCode {
  return {
    id: "promo_1",
    code: "VIP",
    active: true,
    times_redeemed: 3,
    max_redemptions: null,
    expires_at: null,
    created_at: "2026-11-15T12:00:00Z",
    amount_off_pence: 3901,
    percent_off: null,
    currency: "gbp",
    final_price_pence: 7999,
    watch_price_pence: 11900,
    ...over,
  };
}

function show() {
  render(
    <MemoryRouter>
      <DiscountsPage />
    </MemoryRouter>,
  );
}

describe("DiscountsPage", () => {
  beforeEach(() => {
    mocks.list.mockReset();
    mocks.create.mockReset();
    mocks.setActive.mockReset();
  });

  it("lists codes with the price they give and their uses", async () => {
    mocks.list.mockResolvedValue({ watch_price_pence: 11900, codes: [code()] });
    show();
    expect(await screen.findByText("VIP")).toBeTruthy();
    expect(screen.getByText("£79.99")).toBeTruthy();
    expect(screen.getByText("−£39.01")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText(/Watch price £119.00/)).toBeTruthy();
  });

  it("creates a code from the price with the code, in pence", async () => {
    mocks.list.mockResolvedValue({ watch_price_pence: 11900, codes: [] });
    mocks.create.mockResolvedValue(code());
    show();
    fireEvent.click(await screen.findByRole("button", { name: /New code/ }));
    fireEvent.change(screen.getByLabelText("Code"), { target: { value: "vip" } });
    fireEvent.change(screen.getByLabelText(/Watch price with this code/), { target: { value: "79.99" } });
    expect(screen.getByText(/£39.01 off the £119.00 price/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Create code" }));
    await waitFor(() =>
      expect(mocks.create).toHaveBeenCalledWith({ code: "VIP", final_price_pence: 7999, max_redemptions: null, expires_at: null }),
    );
    expect(await screen.findByText("VIP")).toBeTruthy();
  });

  it("refuses a price at or above the watch price", async () => {
    mocks.list.mockResolvedValue({ watch_price_pence: 11900, codes: [] });
    show();
    fireEvent.click(await screen.findByRole("button", { name: /New code/ }));
    fireEvent.change(screen.getByLabelText("Code"), { target: { value: "FREE" } });
    fireEvent.change(screen.getByLabelText(/Watch price with this code/), { target: { value: "119" } });
    fireEvent.click(screen.getByRole("button", { name: "Create code" }));
    expect(await screen.findByText(/under £119.00/)).toBeTruthy();
    expect(mocks.create).not.toHaveBeenCalled();
  });

  it("switches a code off", async () => {
    mocks.list.mockResolvedValue({ watch_price_pence: 11900, codes: [code()] });
    mocks.setActive.mockResolvedValue(code({ active: false }));
    show();
    fireEvent.click(await screen.findByRole("switch"));
    await waitFor(() => expect(mocks.setActive).toHaveBeenCalledWith("promo_1", false));
    expect(await screen.findByText("Off")).toBeTruthy();
  });
});
