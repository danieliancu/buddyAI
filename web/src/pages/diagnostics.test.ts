import { describe, expect, it } from "vitest";
import type { Incident } from "../api";
import { apiFilters, dayStart, filtersFromParams, flatten, hasFilters, matches, mergeLive, paramsFromFilters, techValue } from "./diagnostics";

const inc = (id: number, at: string, extra: Partial<Incident> = {}): Incident => ({
  id,
  device_id: "w1",
  device_name: "Kitchen",
  category: "connection",
  severity: "warn",
  confidence: "confirmed",
  reason_code: "wifi_lost",
  title: "t",
  cause: "c",
  suspected_component: "wifi",
  detected_by: "watch",
  session_id: "s",
  turn_id: null,
  fw_version: "0.1.1",
  event_count: 1,
  related_count: 0,
  legacy: false,
  occurred_at: at,
  recovered_at: null,
  updated_at: at,
  ...extra,
});

describe("URL filters", () => {
  it("round-trips and ignores invalid values", () => {
    const f = filtersFromParams(new URLSearchParams("category=server&severity=error&recovered=no&from=2026-10-01&to=bad&confidence=maybe"));
    expect(f).toEqual({ category: "server", device: "", severity: "error", confidence: "", recovered: "no", from: "2026-10-01", to: "" });
    expect(filtersFromParams(paramsFromFilters(f))).toEqual(f);
    expect(hasFilters(f)).toBe(true);
    expect(hasFilters({ ...filtersFromParams(new URLSearchParams()), category: "watch" })).toBe(false);
  });

  it("maps to the API: inclusive end day, recovered as a boolean", () => {
    const f = filtersFromParams(new URLSearchParams("device=w1&recovered=yes&from=2026-10-01&to=2026-10-02"));
    const a = apiFilters(f);
    expect(a.deviceId).toBe("w1");
    expect(a.recovered).toBe(true);
    expect(a.since).toBe(dayStart("2026-10-01"));
    expect(a.until).toBe(dayStart("2026-10-03"));
  });
});

describe("live updates", () => {
  const all = filtersFromParams(new URLSearchParams());
  const list = [inc(3, "2026-10-08T10:00:00Z"), inc(1, "2026-10-08T08:00:00Z")];

  it("inserts a new incident in time order", () => {
    const out = mergeLive(list, inc(2, "2026-10-08T09:00:00Z"), all, true);
    expect(out.map((i) => i.id)).toEqual([3, 2, 1]);
  });

  it("replaces a regrouped incident instead of duplicating it", () => {
    const out = mergeLive(list, inc(1, "2026-10-08T07:00:00Z", { category: "server", event_count: 3 }), all, true);
    expect(out.map((i) => [i.id, i.event_count])).toEqual([[3, 1], [1, 3]]);
  });

  it("drops an incident that no longer matches the filters", () => {
    const f = { ...all, category: "connection" as const };
    expect(mergeLive(list, inc(3, "2026-10-08T10:00:00Z", { category: "watch" }), f, true).map((i) => i.id)).toEqual([1]);
    expect(matches(inc(9, "2026-10-08T10:00:00Z", { recovered_at: "2026-10-08T10:01:00Z" }), { ...all, recovered: "no" })).toBe(false);
  });

  it("does not pull in incidents older than an incomplete page", () => {
    expect(mergeLive(list, inc(0, "2026-10-01T00:00:00Z"), all, false)).toBe(list);
    expect(mergeLive(list, inc(0, "2026-10-01T00:00:00Z"), all, true).map((i) => i.id)).toEqual([3, 1, 0]);
  });
});

describe("technical values", () => {
  it("names ESP-IDF errors, close codes, errno and units", () => {
    expect(techValue("ws.tls", 0x8001)).toBe("0x8001 ESP_ERR_ESP_TLS_CANNOT_RESOLVE_HOSTNAME");
    expect(techValue("ws.type", 2)).toBe("2 (pong timeout)");
    expect(techValue("ws.errno", 104)).toBe("104 (ECONNRESET)");
    expect(techValue("ws.close", 1012)).toBe("1012 (service restart)");
    expect(techValue("code", 1006)).toBe("1006 (abnormal (no close frame))");
    expect(techValue("wifi_reason", 201)).toBe("201 (no AP found)");
    expect(techValue("rssi", -82)).toBe("-82 dBm");
    expect(techValue("min_heap", 20480)).toBe("20 KB");
    expect(techValue("offline_ms", 65000)).toBe("1 min 5 s");
    expect(techValue("mid_turn", true)).toBe("yes");
  });

  it("flattens nested evidence and skips empty values", () => {
    expect(flatten({ mid_turn: false, ws: { tls: 1, errno: 104 }, note: "", x: null })).toEqual([
      ["mid_turn", false],
      ["ws.tls", 1],
      ["ws.errno", 104],
    ]);
  });
});
