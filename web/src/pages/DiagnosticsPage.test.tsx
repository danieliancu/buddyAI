import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Incident, IncidentDetail, LiveEvent } from "../api";
import DiagnosticsPage from "./DiagnosticsPage";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  clear: vi.fn(),
  devices: vi.fn(),
  live: { handler: null as null | ((e: LiveEvent) => void) },
}));
vi.mock("../api", async (orig) => {
  const real = await orig<typeof import("../api")>();
  return {
    ...real,
    api: {
      ...real.api,
      devices: { ...real.api.devices, list: mocks.devices },
      incidents: { list: mocks.list, get: mocks.get, clear: mocks.clear },
    },
  };
});
vi.mock("../live", () => ({
  useLive: (h: (e: LiveEvent) => void) => {
    mocks.live.handler = h;
    return { connected: true };
  },
}));

const wifi: Incident = {
  id: 7,
  device_id: "w1",
  device_name: "Kitchen watch",
  category: "connection",
  severity: "warn",
  confidence: "confirmed",
  reason_code: "wifi_lost",
  title: "The watch lost its Wi-Fi connection",
  cause: "The Wi-Fi link between the watch and the access point dropped.",
  suspected_component: "wifi",
  detected_by: "watch",
  session_id: "abc",
  turn_id: 4,
  fw_version: "0.1.1",
  event_count: 3,
  related_count: 2,
  legacy: false,
  occurred_at: "2026-10-08T09:00:00Z",
  recovered_at: "2026-10-08T09:00:06Z",
  updated_at: "2026-10-08T09:00:06Z",
};

const detail: IncidentDetail = {
  ...wifi,
  explanation: {
    what: "The watch lost its Wi-Fi connection",
    where: "Between the watch and the server (Wi-Fi, the customer's internet or the network path).",
    cause: "Confirmed: The Wi-Fi link between the watch and the access point dropped.",
    affected: "A conversation was cut off before the answer finished.",
    recovered: "Yes - recovered at 08 Oct 2026, 09:00:06 UTC.",
    next_step: "Check the customer's Wi-Fi and router.",
  },
  events: [
    {
      id: 1, kind: "disconnect", reason: "wifi_lost", detected_by: "watch", category: "connection", confidence: "confirmed",
      reason_code: "wifi_lost", title: "The watch lost its Wi-Fi connection", suspected_component: "wifi", severity: "warn",
      session_id: "abc", turn_id: 4, fw_version: "0.1.1", detail: { wifi_reason: 200, rssi: -74, ws: { tls: 0x8001 } },
      occurred_at: "2026-10-08T09:00:00Z", received_at: "2026-10-08T09:00:06Z", primary: true,
    },
    {
      id: 2, kind: "turn_interrupted", reason: "", detected_by: "server", category: "undetermined", confidence: "unknown",
      reason_code: "turn_interrupted", title: "A conversation was cut off", suspected_component: null, severity: "warn",
      session_id: "abc", turn_id: 4, fw_version: "0.1.1", detail: {}, occurred_at: "2026-10-08T09:00:02Z",
      received_at: "2026-10-08T09:00:02Z", primary: false,
    },
  ],
};

const renderPage = (url = "/admin/diagnostics") =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <DiagnosticsPage />
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  mocks.devices.mockResolvedValue([{ id: "w1", name: "Kitchen watch", online: true }]);
  mocks.list.mockResolvedValue({ items: [wifi], next_cursor: null, counts: { connection: 1, server: 2 } });
  mocks.get.mockResolvedValue(detail);
});

describe("ola Diagnostics page", () => {
  it("shows the incident row with its category, cause, recovery and related events", async () => {
    renderPage();
    expect(screen.getByRole("heading", { name: /ola Diagnostics/ })).toBeTruthy();
    const row = await screen.findByRole("button", { name: /lost its Wi-Fi connection/ });
    expect(row.textContent).toMatch(/\+2 related/);
    expect(row.textContent).toMatch(/Connection/);
    expect(row.textContent).toMatch(/Recovered/);
    expect(row.textContent).toMatch(/Kitchen watch/);
    const tabs = screen.getByRole("tablist", { name: "Category" });
    expect(within(tabs).getByRole("tab", { name: /All 3/ })).toBeTruthy();
    expect(within(tabs).getByRole("tab", { name: /Server 2/ })).toBeTruthy();
  });

  it("expands to the simple explanation, then the technical evidence", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /lost its Wi-Fi connection/ }));
    expect(await screen.findByText("What caused it?")).toBeTruthy();
    expect(screen.getByText(/A conversation was cut off before the answer finished/)).toBeTruthy();
    expect(mocks.get).toHaveBeenCalledWith(7);
    fireEvent.click(screen.getByRole("tab", { name: "Technical" }));
    expect(screen.getByText("0x8001 ESP_ERR_ESP_TLS_CANNOT_RESOLVE_HOSTNAME")).toBeTruthy();
    expect(screen.getByText("-74 dBm")).toBeTruthy();
    expect(screen.getByText("wifi_lost", { selector: "dd" })).toBeTruthy();
    expect(screen.getAllByRole("listitem").length).toBeGreaterThan(1);
  });

  it("passes the category tab and filters to the API", async () => {
    renderPage("/admin/diagnostics?severity=error");
    await screen.findByRole("button", { name: /lost its Wi-Fi connection/ });
    expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ severity: "error", category: undefined }));
    fireEvent.click(screen.getByRole("tab", { name: /Server/ }));
    await waitFor(() => expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ category: "server", severity: "error" })));
  });

  it("applies live updates by incident id", async () => {
    renderPage();
    await screen.findByRole("button", { name: /lost its Wi-Fi connection/ });
    act(() => {
      mocks.live.handler?.({
        type: "incident",
        device_id: "w1",
        incident: { ...wifi, id: 8, title: "The server hit an unexpected error", category: "server", occurred_at: "2026-10-08T10:00:00Z" },
      });
    });
    const rows = screen.getAllByRole("button", { expanded: false });
    expect(rows[0].textContent).toMatch(/unexpected error/);
    act(() => {
      mocks.live.handler?.({ type: "incident", device_id: "w1", incident: { ...wifi, related_count: 3, event_count: 4 } });
    });
    expect(screen.getAllByText(/lost its Wi-Fi connection/)).toHaveLength(1);
    expect(screen.getByText("+3 related")).toBeTruthy();
  });

  it("has honest empty states", async () => {
    mocks.list.mockResolvedValue({ items: [], next_cursor: null, counts: {} });
    renderPage();
    expect(await screen.findByText(/No incidents - all quiet/)).toBeTruthy();
    renderPage("/admin/diagnostics?category=watch");
    expect(await screen.findByText(/No matching incidents/)).toBeTruthy();
  });

  it("loads more with the cursor", async () => {
    mocks.list.mockResolvedValueOnce({ items: [wifi], next_cursor: "CUR", counts: { connection: 2 } });
    mocks.list.mockResolvedValueOnce({ items: [{ ...wifi, id: 6, occurred_at: "2026-10-07T09:00:00Z" }], next_cursor: null, counts: {} });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Load 50 more/ }));
    await waitFor(() => expect(screen.getAllByText(/lost its Wi-Fi connection/)).toHaveLength(2));
    expect(mocks.list).toHaveBeenLastCalledWith(expect.anything(), "CUR");
    expect(screen.queryByRole("button", { name: /Load 50 more/ })).toBeNull();
  });
});
