import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountCost, AccountCosts } from "../api";
import { AccountCostsCard } from "./FinanceTabs";

const accountCosts = vi.hoisted(() => vi.fn());
vi.mock("../api", async (orig) => {
  const real = await orig<typeof import("../api")>();
  return { ...real, api: { ...real.api, accountCosts } };
});

const row = (over: Partial<AccountCost>): AccountCost => ({
  account_id: 1,
  account: "ann***@example.com",
  internal: false,
  period_start: "2026-10-08T00:00:00Z",
  period_end: "2026-11-08T00:00:00Z",
  limit: 1000,
  interactions: 300,
  used_pct: 30,
  cost: 0.9,
  interactive_cost: 0.8,
  background_cost: 0.1,
  by_group: { llm: 0.6, stt: 0.2, embedding: 0.1 },
  average_cost: 0.003,
  projected_cost: 3.0,
  projection: "estimate",
  unpriced_rows: 0,
  target: 2.5,
  status: "within",
  ...over,
});

const body = (accounts: AccountCost[]): AccountCosts => ({
  thresholds: { warn: 2, target: 2.5, critical: 5 },
  min_sample: 20,
  status_counts: { within: 1, critical: 1 },
  accounts,
});

beforeEach(() => accountCosts.mockReset());

describe("AI cost per account (operator)", () => {
  it("shows interactions, cost, average, projection (an estimate) and status", async () => {
    accountCosts.mockResolvedValue(body([
      row({}),
      row({ account_id: 2, account: "bob***@example.com", interactions: 5, used_pct: 0, cost: 5.2, average_cost: 1.04,
            projected_cost: null, projection: "insufficient_data", status: "critical", unpriced_rows: 2 }),
    ]));
    render(<MemoryRouter><AccountCostsCard /></MemoryRouter>);
    expect(await screen.findByText("ann***@example.com")).toBeTruthy();
    expect(screen.getByText("300 / 1,000", { exact: false })).toBeTruthy();
    expect(screen.getByText("~£3.00")).toBeTruthy();
    expect(screen.getByText("not enough data")).toBeTruthy(); // fewer than 20 interactions: no projection
    expect(screen.getByText("Critical cost")).toBeTruthy();
    expect(screen.getByText("+?")).toBeTruthy(); // unpriced usage is not treated as free
    expect(screen.getByText(/Monitoring only: customers can always use all their interactions/)).toBeTruthy();
    expect(screen.getAllByText(/Embeddings £0.10/)).toHaveLength(2); // the group label, not the raw key
  });

  it("filters by status and sorts", async () => {
    accountCosts.mockResolvedValue(body([row({})]));
    render(<MemoryRouter><AccountCostsCard /></MemoryRouter>);
    await screen.findByText("ann***@example.com");
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "critical" } });
    await waitFor(() => expect(accountCosts).toHaveBeenLastCalledWith({ status: "critical", sort: "cost" }));
    fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "projected" } });
    await waitFor(() => expect(accountCosts).toHaveBeenLastCalledWith({ status: "critical", sort: "projected" }));
  });
});
