import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Incident, IncidentDetail, IncidentStats, LiveEvent } from "../api";
import DiagnosticsPage from "./DiagnosticsPage";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  clear: vi.fn(),
  stats: vi.fn(),
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
      incidents: { list: mocks.list, get: mocks.get, clear: mocks.clear, stats: mocks.stats },
    },
  };
});
vi.mock("../live", () => ({
  useLive: (h: (e: LiveEvent) => void) => {
    mocks.live.handler = h;
    return { connected: true };
  },
}));
// jsdom has no layout (ResizeObserver): the charts are replaced by a marker with their data size.
vi.mock("recharts", () => {
  const Pass = ({ children }: { children?: unknown }) => <>{children}</>;
  return {
    ResponsiveContainer: Pass,
    AreaChart: ({ data }: { data: unknown[] }) => <div data-testid="chart" data-points={data.length} />,
    Area: () => null,
    Tooltip: () => null,
    YAxis: () => null,
  };
});

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

const stats: IncidentStats = {
  hours: 24,
  buckets: Array.from({ length: 25 }, (_, i) => ({ t: `2026-10-08T${String(i % 24).padStart(2, "0")}:00:00Z`, watch: 0, connection: i === 9 ? 1 : 0, server: 0, undetermined: 0 })),
  totals: { watch: 0, connection: 1, server: 3, undetermined: 0 },
  attention: [
    { category: "server", device_id: "w1", device_name: "Kitchen watch", count: 3, severity: "error", title: "The AI model timed out", latest_id: 9, latest_at: "2026-10-08T10:00:00Z" },
  ],
  open_total: 3,
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
  mocks.stats.mockResolvedValue(stats);
});

describe("ola Diagnostics dashboard", () => {
  it("shows needs-attention tiles, the three trend cards and the grouped list", async () => {
    renderPage();
    expect(screen.getByRole("heading", { name: /ola Diagnostics/ })).toBeTruthy();
    expect(await screen.findByText("The AI model timed out on Kitchen watch")).toBeTruthy();
    expect(screen.getByLabelText("3 open")).toBeTruthy();
    const charts = await screen.findAllByTestId("chart");
    expect(charts).toHaveLength(3);
    expect(charts[0].getAttribute("data-points")).toBe("25");
    expect(screen.getByRole("button", { name: /Server incidents/ }).textContent).toMatch(/3/);
    const row = await screen.findByRole("button", { name: /lost its Wi-Fi connection.*Kitchen watch/ });
    expect(row.textContent).toMatch(/\+2 related/);
    expect(screen.getByText("1 incident")).toBeTruthy(); // the Connection section's count
    expect(screen.getByText("1 of 3 shown")).toBeTruthy();
  });

  it("shows the newest incident in the details panel: simple, then technical, with the timeline", async () => {
    renderPage();
    expect(await screen.findByText("What caused it?")).toBeTruthy();
    expect(mocks.get).toHaveBeenCalledWith(7);
    expect(screen.getByText(/A conversation was cut off before the answer finished/)).toBeTruthy();
    expect(screen.getByText("Reported by the watch", { exact: false })).toBeTruthy(); // timeline
    fireEvent.click(screen.getByRole("tab", { name: "Technical" }));
    expect(screen.getByText("0x8001 ESP_ERR_ESP_TLS_CANNOT_RESOLVE_HOSTNAME")).toBeTruthy();
    expect(screen.getByText("-74 dBm")).toBeTruthy();
    expect(screen.getByText("wifi_lost", { selector: "dd" })).toBeTruthy();
  });

  it("filters: a trend card selects its category, an attention tile its watch, search goes to the API", async () => {
    renderPage("/admin/diagnostics?severity=error");
    await screen.findByRole("button", { name: /lost its Wi-Fi connection/ });
    expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ severity: "error", category: undefined }));
    fireEvent.click(screen.getByRole("button", { name: /Server incidents/ }));
    await waitFor(() => expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ category: "server", severity: "error" })));
    fireEvent.click(screen.getByText("The AI model timed out on Kitchen watch"));
    await waitFor(() =>
      expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ category: "server", deviceId: "w1", recovered: false, severity: undefined })),
    );
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "dns" } });
    await waitFor(() => expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ q: "dns" })));
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
    expect(screen.getByRole("button", { name: /unexpected error/ })).toBeTruthy();
    act(() => {
      mocks.live.handler?.({ type: "incident", device_id: "w1", incident: { ...wifi, related_count: 3, event_count: 4 } });
    });
    const list = screen.getByRole("heading", { name: "Incidents" }).closest("section")!;
    expect(within(list).getAllByText(/lost its Wi-Fi connection/)).toHaveLength(1);
    expect(within(list).getByText(/\+3 related/)).toBeTruthy();
  });

  it("has honest empty states", async () => {
    mocks.list.mockResolvedValue({ items: [], next_cursor: null, counts: {} });
    mocks.stats.mockResolvedValue({ ...stats, attention: [], open_total: 0 });
    renderPage();
    expect(await screen.findByText(/No incidents - all quiet/)).toBeTruthy();
    expect(await screen.findByText(/Nothing needs attention/)).toBeTruthy();
    renderPage("/admin/diagnostics?category=watch");
    expect(await screen.findByText(/No matching incidents/)).toBeTruthy();
  });

  it("loads more with the cursor", async () => {
    mocks.list.mockResolvedValueOnce({ items: [wifi], next_cursor: "CUR", counts: { connection: 2 } });
    mocks.list.mockResolvedValueOnce({ items: [{ ...wifi, id: 6, occurred_at: "2026-10-07T09:00:00Z" }], next_cursor: null, counts: {} });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Load 50 more/ }));
    const list = screen.getByRole("heading", { name: "Incidents" }).closest("section")!;
    await waitFor(() => expect(within(list).getAllByText(/lost its Wi-Fi connection/)).toHaveLength(2));
    expect(mocks.list).toHaveBeenLastCalledWith(expect.anything(), "CUR");
    expect(screen.queryByRole("button", { name: /Load 50 more/ })).toBeNull();
  });
});
