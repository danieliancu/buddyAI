// Small typed client for the BuddyAI FastAPI backend (same origin, cookie session).

// ---------- types ----------

export type UiState = "idle" | "listening" | "thinking" | "speaking" | string;

export interface AuthStatus {
  needs_setup: boolean;
  user: string | null;
}

export interface Device {
  id: string;
  name: string;
  hw_model: string;
  fw_version: string;
  paired_at: string | null;
  last_seen_at: string | null;
  last_ip: string | null;
  battery_pct: number | null;
  charging: boolean | null;
  rssi: number | null;
  online: boolean;
  state: UiState | null;
}

export interface PendingPairing {
  device_id: string;
  hw_model: string;
  expires_in_s: number;
}

export interface Theme {
  preset: string;
  accent: string;
  background: string;
  clock: string;
  text: string;
}

export type Language = "ro" | "en";
export type VadSensitivity = "low" | "medium" | "high";

export interface DeviceSettings {
  language: Language;
  volume: number;
  brightness: number;
  screen_timeout_s: number;
  time_24h: boolean;
  timezone: string;
  theme: Theme;
  max_listen_s: number;
  persona_id: number | null;
  custom_instructions: string;
  llm_model: string | null;
  tts_voice_ro: string | null;
  tts_voice_en: string | null;
  speech_rate: number;
  vad_sensitivity: VadSensitivity;
  max_reply_chars: number;
  history_turns: number;
}

export type SettingsPatch = Partial<Omit<DeviceSettings, "theme">> & { theme?: Partial<Theme> };

export interface SettingsResponse {
  settings: DeviceSettings;
  version: number;
}

export interface Options {
  llm_models: { id: string; provider: string; label: string }[];
  tts: Record<string, { voices: string[]; default_voice: string }>;
  theme_presets: Record<string, Omit<Theme, "preset">>;
  languages: { id: Language; label: string }[];
  vad_sensitivity: VadSensitivity[];
}

export interface Persona {
  id: number;
  name: string;
  system_prompt: string;
  is_default: boolean;
  created_at: string;
}

export type PersonaInput = Pick<Persona, "name" | "system_prompt" | "is_default">;

export type TurnStatus = "active" | "completed" | "aborted" | "error" | "no_speech" | string;

export interface ConversationTurn {
  id: number;
  status: TurnStatus;
  language: string;
  user_text: string;
  assistant_text: string;
  ttfa_ms: number | null;
  created_at: string;
}

export interface Conversation {
  id: number;
  started_at: string;
  last_activity_at: string;
  turns: ConversationTurn[];
}

export interface UsageItem {
  kind: string;
  provider: string;
  model: string;
  unit: string;
  quantity: number;
  cost_usd: number;
  /** In the display currency (GBP). */
  cost: number;
  unpriced: number;
}

export interface Currency {
  currency: string;
  /** 1 USD = usd_rate units of `currency`. */
  usd_rate: number;
  symbol: string;
}

export interface Usage extends Currency {
  days: number;
  total_cost: number;
  total_cost_usd: number;
  items: UsageItem[];
  by_day: { day: string; cost_usd: number; cost: number }[];
}

export interface Stats {
  count: number;
  p50: number | null;
  p95: number | null;
}

export interface RecentTurn {
  id: number;
  device_id: string;
  language: string;
  status: TurnStatus;
  created_at: string;
  stt_ms: number | null;
  llm_first_token_ms: number | null;
  tts_first_audio_ms: number | null;
  ttfa_ms: number | null;
  error: string | null;
}

export interface Diagnostics {
  days: number;
  targets: { ttfa_p50_ms: number; ttfa_p95_ms: number };
  ttfa: Stats;
  stages: {
    stt_ms: Stats;
    llm_first_token_ms: Stats;
    tts_first_audio_ms: Stats;
    ttfa_server_ms: Stats;
    ttfa_device_ms: Stats;
  };
  by_language: Record<string, Stats>;
  by_provider: Record<string, Stats>;
  status_counts: Record<string, number>;
  recent: RecentTurn[];
}

export interface PricingRule {
  id: number;
  provider: string;
  model: string;
  unit: string;
  price_usd: number;
  /** Unit price in the display currency (GET /api/pricing only). */
  price_display?: number;
  note: string;
  updated_at: string;
}

export type KeyName = "openai_api_key" | "dashscope_api_key" | "azure_speech_key" | "azure_speech_region";
export type AiProfile = "openai" | "qwen";

export interface SystemInfo {
  version: string;
  device_ws_url: string;
  base_url: string;
  mdns_enabled: boolean;
  mock_providers: boolean;
  ai_profile?: AiProfile | string;
  keys: Partial<Record<KeyName, string>>;
}

export type TestTarget = "llm" | "stt" | "tts_ro" | "tts_en";

export interface TestResult {
  ok: boolean;
  detail: string;
}

export interface FirmwareRelease {
  id: number;
  version: string;
  filename: string;
  sha256: string;
  size: number;
  notes: string;
  uploaded_at: string;
}

export interface OtaOffer {
  ok: boolean;
  version: string;
  url: string;
  sha256: string;
  size: number;
}

// ---------- errors ----------

export interface FieldError {
  loc: (string | number)[];
  msg: string;
  type?: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(ApiError.describe(status, detail));
  }

  /** Pydantic-style field errors (422), with the leading "body" segment stripped. */
  get fieldErrors(): FieldError[] {
    if (!Array.isArray(this.detail)) return [];
    return this.detail
      .filter((e): e is FieldError => typeof e === "object" && e !== null && "msg" in e)
      .map((e) => ({ ...e, loc: (e.loc ?? []).filter((p, i) => !(i === 0 && p === "body")) }));
  }

  static describe(status: number, detail: unknown): string {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((e) => (e && typeof e === "object" && "msg" in e ? `${(e.loc ?? []).join(".")}: ${e.msg}` : String(e)))
        .join("; ");
    }
    if (status === 0) return "Server not reachable";
    return `Error ${status}`;
  }
}

// ---------- core ----------

let unauthorizedHandler: (() => void) | null = null;

/** Called on any 401 from a protected endpoint (global redirect to login). */
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  unauthorizedHandler = fn;
}

export function notifyUnauthorized(): void {
  unauthorizedHandler?.();
}

interface RequestOpts {
  /** Do not trigger the global 401 handler (login form, status probe). */
  no401?: boolean;
}

async function request<T>(method: string, path: string, body?: unknown, opts: RequestOpts = {}): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin", headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, "Server not reachable");
  }
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    if (res.status === 401 && !opts.no401) notifyUnauthorized();
    const detail = data && typeof data === "object" && "detail" in data ? (data as { detail: unknown }).detail : data;
    throw new ApiError(res.status, detail ?? res.statusText);
  }
  return data as T;
}

const get = <T>(p: string, o?: RequestOpts) => request<T>("GET", p, undefined, o);
const post = <T>(p: string, b?: unknown, o?: RequestOpts) => request<T>("POST", p, b ?? {}, o);
const put = <T>(p: string, b: unknown) => request<T>("PUT", p, b);
const patch = <T>(p: string, b: unknown) => request<T>("PATCH", p, b);
const del = <T>(p: string) => request<T>("DELETE", p);

const q = (params: Record<string, string | number | undefined | null>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  const str = s.toString();
  return str ? `?${str}` : "";
};
const enc = encodeURIComponent;

// ---------- endpoints ----------

export const api = {
  auth: {
    status: () => get<AuthStatus>("/api/auth/status", { no401: true }),
    setup: (username: string, password: string) =>
      post<{ user: string }>("/api/auth/setup", { username, password }, { no401: true }),
    login: (username: string, password: string) =>
      post<{ user: string }>("/api/auth/login", { username, password }, { no401: true }),
    logout: () => post<{ ok: boolean }>("/api/auth/logout", {}, { no401: true }),
    me: () => get<{ user: string }>("/api/auth/me"),
  },
  devices: {
    list: () => get<Device[]>("/api/devices"),
    pending: () => get<PendingPairing[]>("/api/devices/pending"),
    pair: (code: string, name: string) => post<{ device_id: string }>("/api/devices/pair", { code, name }),
    rename: (id: string, name: string) => patch<Device>(`/api/devices/${enc(id)}`, { name }),
    revoke: (id: string) => del<{ ok: boolean }>(`/api/devices/${enc(id)}`),
    settings: (id: string) => get<SettingsResponse>(`/api/devices/${enc(id)}/settings`),
    patchSettings: (id: string, changes: SettingsPatch) =>
      patch<SettingsResponse>(`/api/devices/${enc(id)}/settings`, changes),
    conversations: (id: string) => get<Conversation[]>(`/api/devices/${enc(id)}/conversations`),
    deleteHistory: (id: string) => del<{ deleted_turns: number }>(`/api/devices/${enc(id)}/conversations`),
    ota: (id: string, releaseId: number) => post<OtaOffer>(`/api/devices/${enc(id)}/ota`, { release_id: releaseId }),
  },
  options: () => get<Options>("/api/options"),
  personas: {
    list: () => get<Persona[]>("/api/personas"),
    create: (p: PersonaInput) => post<Persona>("/api/personas", p),
    update: (id: number, p: PersonaInput) => put<Persona>(`/api/personas/${id}`, p),
    remove: (id: number) => del<{ ok: boolean }>(`/api/personas/${id}`),
  },
  usage: (days: number, deviceId?: string) => get<Usage>(`/api/usage${q({ days, device_id: deviceId })}`),
  diagnostics: (days: number, deviceId?: string) =>
    get<Diagnostics>(`/api/diagnostics${q({ days, device_id: deviceId })}`),
  pricing: {
    list: () => get<PricingRule[]>("/api/pricing"),
    update: (id: number, price_usd: number, note: string | null) =>
      put<PricingRule>(`/api/pricing/${id}`, { price_usd, note }),
    currency: () => get<Currency>("/api/pricing/currency"),
    setCurrency: (currency: string, usd_rate: number) => put<Currency>("/api/pricing/currency", { currency, usd_rate }),
  },
  system: {
    info: () => get<SystemInfo>("/api/system/info"),
    setKeys: (keys: Partial<Record<KeyName, string>>) => put<SystemInfo>("/api/system/keys", keys),
    test: (target: TestTarget) => post<TestResult>(`/api/system/test/${target}`),
  },
  firmware: {
    list: () => get<FirmwareRelease[]>("/api/firmware"),
    upload: (file: File, version: string, notes: string) => {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("version", version);
      fd.append("notes", notes);
      return post<FirmwareRelease>("/api/firmware", fd);
    },
  },
};

// ---------- live events (/api/live) ----------

interface LiveBase {
  device_id: string;
  at?: number;
}

export type LiveEvent =
  | ({ type: "device_online" } & LiveBase)
  | ({ type: "device_offline" } & LiveBase)
  | ({ type: "device_state"; state: UiState } & LiveBase)
  | ({ type: "device_status"; battery_pct: number | null; charging: boolean | null; rssi: number | null } & LiveBase)
  | ({
      type: "turn_end";
      turn_id: number | string;
      status: TurnStatus;
      user_text: string;
      assistant_text: string;
      stt_ms: number | null;
      llm_first_token_ms: number | null;
      tts_first_audio_ms: number | null;
      ttfa_server_ms: number | null;
      ttfa_device_ms: number | null;
    } & LiveBase)
  | ({ type: "playback_done"; turn_id: number | string } & LiveBase)
  | ({ type: "pairing_pending" } & LiveBase)
  | ({ type: "device_paired" } & LiveBase)
  | ({ type: "settings_changed" } & LiveBase)
  | { type: "keepalive"; at?: number };
