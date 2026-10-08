/** ola Diagnostics page helpers: URL filters, live merging, readable technical values (pure, unit-tested). */
import type { Confidence, Incident, IncidentCategory, IncidentFilters, IncidentSeverity } from "../api";

export const CATEGORIES: { id: IncidentCategory; label: string; tone: "accent" | "warn" | "danger" | "neutral"; hint: string }[] = [
  { id: "watch", label: "Watch", tone: "accent", hint: "Firmware, restarts, memory, power" },
  { id: "connection", label: "Connection", tone: "warn", hint: "Wi-Fi, DNS, TLS, network" },
  { id: "server", label: "Server", tone: "danger", hint: "ola server, database, AI providers" },
  { id: "undetermined", label: "Undetermined", tone: "neutral", hint: "Not enough evidence for a cause" },
];
export const CATEGORY = Object.fromEntries(CATEGORIES.map((c) => [c.id, c])) as Record<IncidentCategory, (typeof CATEGORIES)[number]>;

export const SEVERITY: Record<IncidentSeverity, { tone: "danger" | "warn" | "neutral"; label: string }> = {
  error: { tone: "danger", label: "Problem" },
  warn: { tone: "warn", label: "Warning" },
  info: { tone: "neutral", label: "Info" },
};

export const CONFIDENCE: Record<Confidence, { label: string; hint: string }> = {
  confirmed: { label: "Confirmed", hint: "Direct evidence of the cause" },
  probable: { label: "Probable", hint: "The evidence points to this cause but does not prove it" },
  unknown: { label: "Unknown", hint: "The evidence does not establish a cause" },
};

/** The page's filters, kept in the URL (shareable, survive a reload). Dates are yyyy-mm-dd (local). */
export interface PageFilters {
  category: IncidentCategory | "";
  device: string;
  severity: IncidentSeverity | "";
  confidence: Confidence | "";
  recovered: "" | "yes" | "no";
  from: string;
  to: string;
}

const KEYS: (keyof PageFilters)[] = ["category", "device", "severity", "confidence", "recovered", "from", "to"];

export function filtersFromParams(p: URLSearchParams): PageFilters {
  const pick = <T extends string>(v: string | null, allowed: readonly T[]): T | "" => (v && (allowed as readonly string[]).includes(v) ? (v as T) : "");
  const date = (v: string | null) => (v && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : "");
  return {
    category: pick(p.get("category"), CATEGORIES.map((c) => c.id)),
    device: p.get("device") ?? "",
    severity: pick(p.get("severity"), ["error", "warn", "info"] as const),
    confidence: pick(p.get("confidence"), ["confirmed", "probable", "unknown"] as const),
    recovered: pick(p.get("recovered"), ["yes", "no"] as const),
    from: date(p.get("from")),
    to: date(p.get("to")),
  };
}

export function paramsFromFilters(f: PageFilters): URLSearchParams {
  const p = new URLSearchParams();
  for (const k of KEYS) if (f[k]) p.set(k, f[k]);
  return p;
}

export const hasFilters = (f: PageFilters, ignoreCategory = true) =>
  KEYS.some((k) => (ignoreCategory && k === "category" ? false : Boolean(f[k])));

/** Local calendar day -> ISO instant (start of that day); `to` is inclusive, so it ends at the next midnight. */
export function dayStart(day: string, plusDays = 0): string {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d + plusDays).toISOString();
}

export function apiFilters(f: PageFilters, withCategory = true): IncidentFilters {
  return {
    category: withCategory && f.category ? f.category : undefined,
    deviceId: f.device || undefined,
    severity: f.severity || undefined,
    confidence: f.confidence || undefined,
    recovered: f.recovered === "" ? undefined : f.recovered === "yes",
    since: f.from ? dayStart(f.from) : undefined,
    until: f.to ? dayStart(f.to, 1) : undefined,
  };
}

export function matches(i: Incident, f: PageFilters): boolean {
  const t = Date.parse(i.occurred_at);
  if (f.category && i.category !== f.category) return false;
  if (f.device && i.device_id !== f.device) return false;
  if (f.severity && i.severity !== f.severity) return false;
  if (f.confidence && i.confidence !== f.confidence) return false;
  if (f.recovered === "yes" && !i.recovered_at) return false;
  if (f.recovered === "no" && i.recovered_at) return false;
  if (f.from && t < Date.parse(dayStart(f.from))) return false;
  if (f.to && t >= Date.parse(dayStart(f.to, 1))) return false;
  return true;
}

const newestFirst = (a: Incident, b: Incident) => Date.parse(b.occurred_at) - Date.parse(a.occurred_at) || b.id - a.id;

/** A live update: insert or replace by id (an incident is regrouped as evidence arrives), keep the order, drop
 * it if it no longer matches the filters. An incident older than the loaded page is not pulled in. */
export function mergeLive(list: Incident[], inc: Incident, f: PageFilters, complete: boolean): Incident[] {
  const rest = list.filter((x) => x.id !== inc.id);
  if (!matches(inc, f)) return rest;
  const oldest = list[list.length - 1];
  if (!complete && oldest && newestFirst(inc, oldest) > 0 && !list.some((x) => x.id === inc.id)) return list;
  return [...rest, inc].sort(newestFirst);
}

// ---- technical values ----

const ESP_TLS: Record<number, string> = {
  0x8001: "ESP_ERR_ESP_TLS_CANNOT_RESOLVE_HOSTNAME",
  0x8002: "ESP_ERR_ESP_TLS_CANNOT_CREATE_SOCKET",
  0x8003: "ESP_ERR_ESP_TLS_UNSUPPORTED_PROTOCOL_FAMILY",
  0x8004: "ESP_ERR_ESP_TLS_FAILED_CONNECT_TO_HOST",
  0x8005: "ESP_ERR_ESP_TLS_SOCKET_SETOPT_FAILED",
  0x8006: "ESP_ERR_ESP_TLS_CONNECTION_TIMEOUT",
  0x8007: "ESP_ERR_ESP_TLS_SE_FAILED",
  0x8008: "ESP_ERR_ESP_TLS_TCP_CLOSED_FIN",
  0x8009: "ESP_ERR_ESP_TLS_SERVER_HANDSHAKE_TIMEOUT",
  0x8015: "ESP_ERR_MBEDTLS_X509_CRT_PARSE_FAILED",
  0x8018: "ESP_ERR_MBEDTLS_SSL_WRITE_FAILED",
  0x801a: "ESP_ERR_MBEDTLS_SSL_HANDSHAKE_FAILED",
  0x801d: "ESP_ERR_MBEDTLS_SSL_READ_FAILED",
};
const WS_TYPE: Record<number, string> = { 1: "TCP transport", 2: "pong timeout", 3: "handshake", 4: "server close" };
const ERRNO: Record<number, string> = {
  104: "ECONNRESET", 110: "ETIMEDOUT", 111: "ECONNREFUSED", 113: "EHOSTUNREACH", 118: "EHOSTUNREACH",
  103: "ECONNABORTED", 128: "ENOTCONN", 32: "EPIPE", 5: "EIO", 11: "EAGAIN",
};
const CLOSE: Record<number, string> = {
  1000: "normal", 1001: "going away", 1006: "abnormal (no close frame)", 1011: "internal error", 1012: "service restart",
  1013: "try again later", 4000: "replaced by a newer connection", 4001: "token revoked", 4002: "disconnected by operator",
};
const WIFI: Record<number, string> = {
  2: "auth expired", 3: "deauthenticated by the AP", 4: "association expired", 8: "left the network",
  15: "4-way handshake timeout", 200: "beacon timeout", 201: "no AP found", 202: "auth failed", 203: "association failed",
  204: "handshake timeout", 205: "connection failed",
};

const hex = (n: number) => `0x${n.toString(16).toUpperCase()}`;

/** One evidence field as a readable value: codes with their ESP-IDF / RFC names, units for sizes and times. */
export function techValue(key: string, v: unknown): string {
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v !== "number") return typeof v === "string" ? v : JSON.stringify(v);
  const leaf = key.split(".").pop() ?? key;
  const named = (name: string | undefined) => (name ? `${v} (${name})` : String(v));
  switch (leaf) {
    case "tls":
      return `${hex(v)}${ESP_TLS[v] ? ` ${ESP_TLS[v]}` : ""}`;
    case "type":
      return key.includes("ws") ? named(WS_TYPE[v]) : String(v);
    case "errno":
      return named(ERRNO[v]);
    case "close":
    case "code":
      return named(CLOSE[v]);
    case "wifi_reason":
      return named(WIFI[v]);
    case "rssi":
      return `${v} dBm`;
    case "heap":
    case "min_heap":
    case "prev_min_heap":
      return `${Math.round(v / 1024)} KB`;
    case "offline_ms":
      return fmtDuration(v / 1000);
    case "uptime_s":
    case "prev_uptime_s":
    case "session_s":
    case "timeout_s":
      return fmtDuration(v);
    case "hs":
      return `HTTP ${v}`;
    default:
      return String(v);
  }
}

/** Flattens an event's detail ({ws: {tls: 1}} -> ["ws.tls", 1]) in a stable order. */
export function flatten(detail: Record<string, unknown>, prefix = ""): [string, unknown][] {
  const out: [string, unknown][] = [];
  for (const [k, v] of Object.entries(detail)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) out.push(...flatten(v as Record<string, unknown>, key));
    else if (v !== null && v !== undefined && v !== "") out.push([key, v]);
  }
  return out;
}

export function fmtDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} s`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
  return `${Math.floor(s / 86400)} d ${Math.floor((s % 86400) / 3600)} h`;
}
