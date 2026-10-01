// Small typed client for the ola FastAPI backend (same origin, cookie session).
//
// Two roles share one browser session cookie (both can be signed in at the same time):
//   - operator ("admin" area): /api/auth, /api/devices, /api/accounts, ... → `api.*`
//   - customer ("me" area):    /api/me/...                                → `api.me.*`
// A 401 is reported to the handler of the area the request belongs to, so the app can send
// operators to /admin/login and customers to /login.

// ---------- types ----------

export type UiState = "idle" | "listening" | "thinking" | "speaking" | string;

export interface AuthStatus {
  needs_setup: boolean;
  user: string | null;
  /** False on production servers: the first operator is created with the CLI, not the browser. */
  web_setup_allowed?: boolean;
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
  /** Operator API only: the customer owning the watch (null = operator stock). */
  account?: AccountRef | null;
}

export interface AccountRef {
  id: number;
  email: string;
  name: string;
}

export type AccountStatus = "active" | "suspended" | "deleted";

/** `accounts.public()` on the server. */
export interface Account {
  id: number;
  email: string;
  name: string;
  /** ISO 3166-1 alpha-2, upper case. */
  country: string | null;
  email_verified: boolean;
  status: AccountStatus | string;
  has_password: boolean;
  created_at: string;
}

/** Row of GET /api/accounts (operator). */
export interface AccountRow extends Account {
  last_login_at: string | null;
  /** Number of paired watches. */
  devices: number;
  /** AI cost this calendar month, in the display currency. */
  month_cost: number;
}

export interface AccountList {
  currency: string;
  symbol: string;
  accounts: AccountRow[];
}

/** Stripe subscription status ("ola Care"). */
export type SubscriptionStatus =
  | "trialing"
  | "active"
  | "past_due"
  | "canceled"
  | "unpaid"
  | "incomplete"
  | "incomplete_expired"
  | string;

export interface SubscriptionInfo {
  status: SubscriptionStatus;
  trial_end: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
}

/** Full subscription row (operator). `source` "complimentary" = operator-granted pilot/test access (no Stripe ids). */
export interface Subscription extends SubscriptionInfo {
  id: number;
  source: "stripe" | "complimentary" | string;
  stripe_subscription_id: string | null;
  stripe_customer_id: string | null;
  account_id: number | null;
  current_period_start: string | null;
  allowance_pence: number | null;
  granted_by: string | null;
  note: string;
  updated_at: string;
}

export type OrderStatus = "paid" | "shipped" | "delivered" | "refunded" | "cancelled" | string;

export interface Order {
  id: number;
  stripe_session_id: string;
  stripe_payment_intent: string | null;
  account_id: number | null;
  email: string;
  /** ISO 4217, lower case ("gbp"). */
  currency: string;
  /** Minor units (pence / cents), including tax and shipping. */
  amount_total: number;
  amount_tax: number;
  amount_shipping: number;
  status: OrderStatus;
  shipping_name: string;
  shipping_address: Record<string, unknown>;
  country: string | null;
  carrier: string;
  tracking_number: string;
  created_at: string;
  shipped_at: string | null;
  delivered_at: string | null;
}

export interface OrderUpdate {
  status: "paid" | "shipped" | "delivered" | "cancelled";
  carrier: string;
  tracking_number: string;
}

/** AI allowance of the current period, in GBP (operator view). */
export interface Allowance {
  used: number;
  limit: number;
  currency: string;
  /** Operator override of the plan allowance (GBP); null = plan default. */
  override: number | null;
  used_pct?: number;
  period_start?: string;
  period_end?: string;
  period_kind?: "stripe" | "complimentary" | "calendar";
  unpriced_rows?: number;
}

/** GET /api/accounts/{id} (operator). */
export interface AccountDetail extends Account {
  last_login_at: string | null;
  devices: Device[];
  subscription: Subscription | null;
  /** The subscription gives access now (Stripe status, or an unexpired complimentary grant). */
  entitled: boolean;
  /** Internal/operator account: never limited (costs still tracked). */
  internal: boolean;
  allowance: Allowance;
  orders: Order[];
}

export interface AuditEntry {
  id: number;
  /** Operator username, "account:<id>" or "system". */
  actor: string;
  action: string;
  account_id: number | null;
  device_id: string | null;
  detail: string;
  created_at: string;
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

/** A language code from options.languages (e.g. "en", "de", "zh"). */
export type Language = string;
/** Device language: "auto" (reply in the language the user speaks) or a language code. */
export type DeviceLanguage = "auto" | Language;

export interface LanguageInfo {
  code: Language;
  /** English name, e.g. "German". */
  name: string;
  /** Name in the language itself, e.g. "Deutsch". */
  native_name: string;
  /** Writing system: "latin", "cyrillic", "greek", "arabic", "hebrew", "han", ... */
  script: string;
  rtl: boolean;
  /** Whether the watch can display text in this script. */
  captions: boolean;
  /** Whether the watch menus, messages and dates are translated into it (otherwise they stay in English). */
  watch_menus?: boolean;
}

export interface TtsVoices {
  voices: string[];
  default_voice: string;
}
export type VadSensitivity = "low" | "medium" | "high";

export interface DeviceSettings {
  language: DeviceLanguage;
  /** The owner's main language, offered on the watch's quick-settings toggle next to Auto/English. */
  preferred_language: Language | null;
  volume: number;
  brightness: number;
  screen_timeout_s: number;
  timezone: string;
  theme: Theme;
  max_listen_s: number;
  wait_for_speech_s: number;
  persona_id: number | null;
  custom_instructions: string;
  llm_model: string | null;
  /** null = the profile's default voice. */
  tts_voice: string | null;
  /** Optional per-language voice, e.g. {"ro": "cedar"}. */
  tts_voice_overrides: Record<Language, string>;
  speech_rate: number;
  vad_sensitivity: VadSensitivity;
  max_reply_chars: number;
  history_turns: number;
  /** The AI may search the internet (weather, addresses, news…); each search is billed. */
  web_search: boolean;
}

export type SettingsPatch = Partial<Omit<DeviceSettings, "theme">> & { theme?: Partial<Theme> };

export interface SettingsResponse {
  settings: DeviceSettings;
  version: number;
}

export interface Options {
  llm_models: { id: string; provider: string; label: string }[];
  /** by_language only lists overrides (languages whose voices differ from the default set). */
  tts: TtsVoices & { by_language: Record<Language, TtsVoices> };
  theme_presets: Record<string, Omit<Theme, "preset">>;
  /** Sorted by English name. */
  languages: LanguageInfo[];
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

/** Customer view of a persona: system personas (own=false, read-only) and the account's own. */
export interface MyPersona extends Persona {
  own: boolean;
}

export type ItemKind = "note" | "reminder";

/** A note (text only) or a reminder (time + short text). Numbered per kind; a deleted number is reused. */
export interface Item {
  kind: ItemKind;
  number: number;
  text: string;
  /** Reminders: when it is due (UTC ISO). */
  due_at: string | null;
  /** Reminders, optional: end of a time range such as 09:30–10:00 (UTC ISO). */
  end_at: string | null;
  /** Reminders, optional: also alert this many minutes before the start (besides the alert at the start). */
  notify_before_min: number | null;
  /** Reminders, optional: where (taken from the text by the assistant, editable). */
  location: string | null;
  /** Reminders, optional: who, comma-separated ("Ana, Mihai"). */
  participants: string | null;
  /** Reminders: the time has passed and it is not completed (stays until completed, deleted or rescheduled). */
  overdue: boolean;
  /** Reminders: marked completed (no longer fires). */
  done: boolean;
  created_at: string;
  updated_at: string;
}

export interface ItemInput {
  kind: ItemKind;
  /** Notes up to 10000 characters, reminders up to 80. */
  text: string;
  /** ISO 8601 with offset; required for reminders. */
  due_at: string | null;
  /** Reminders, optional: ISO 8601 with offset, after due_at. */
  end_at?: string | null;
  /** Reminders, optional: advance notice in minutes. */
  notify_before_min?: number | null;
  location?: string | null;
  participants?: string | null;
}

/** GET /api/me/subscription. */
export interface MySubscription {
  /** False when the server runs without Stripe: hide the whole plan section. */
  billing_enabled: boolean;
  subscription: SubscriptionInfo | null;
  /** 0–100 */
  allowance_used_pct: number;
  orders: Pick<Order, "id" | "status" | "created_at" | "tracking_number" | "carrier">[];
}

/** GET /api/me/usage: completed conversations in the current allowance period (no costs). */
export interface MyUsage {
  period_start: string;
  reset_at: string;
  questions: number;
}

export type PlanKind = "trial" | "active" | "past_due" | "canceled" | "complimentary" | "expired" | "none" | "internal";

/** GET /api/me/plan: ola Care for the customer. Shares of the allowance only, never internal costs. */
export interface MyPlan {
  billing_enabled: boolean;
  /** False: usage limits are not active for this account (no meter). */
  enforced: boolean;
  status: {
    kind: PlanKind;
    trial_end?: string | null;
    period_end?: string | null;
    cancel_at_period_end?: boolean;
    note?: string;
  };
  usage: {
    used_pct: number;
    period_start: string;
    reset_at: string;
    activity_count: number;
    /** Extra usage bought this period, as % of the plan's usage. */
    extra_pct: number;
  };
  thresholds: number[];
  prices: { currency: "GBP"; care_price_pence: number; topup_price_pence: number; topup_adds_pct: number };
  can_subscribe: boolean;
  topup_available: boolean;
  can_manage_billing: boolean;
  topups: { id: number; status: "paid" | "refunded" | string; amount_pence: number; paid_at: string | null; period_end: string; current: boolean }[];
}

export interface UsageNotice {
  threshold: number;
  level: "info" | "warning" | "limit";
}

/** Operator: plan settings (GBP pence). */
export interface BillingSettings {
  enforce: boolean;
  care_price_pence: number;
  care_allowance_pence: number;
  topup_price_pence: number;
  topup_allowance_pence: number;
  thresholds: string;
  usd_gbp_rate: string;
  reserve_pence: number;
  updated_at: string;
  updated_by: string;
  stripe_configured: boolean;
  stripe_mode: "off" | "test" | "live";
  care_price_id_set: boolean;
}

export interface TurnCostStats {
  count: number;
  total?: number;
  mean?: number;
  p50?: number;
  p90?: number;
  p95?: number;
  max?: number;
}

/** Operator: GET /api/finance (GBP; estimates, mock usage excluded). */
export interface Finance {
  days: number;
  currency: "GBP";
  note: string;
  provider_cost: number;
  by_group: Record<string, number>;
  by_day: { day: string; cost: number }[];
  by_month: { month: string; cost: number }[];
  by_account: { account_id: number | null; account: string; cost: number }[];
  by_device: { device_id: string; name: string; cost: number }[];
  turns: {
    completed: TurnCostStats;
    aborted: TurnCostStats;
    error: TurnCostStats;
    no_speech: TurnCostStats;
    not_charged_to_customers: number;
  };
  search: { searches: number; cost: number; cache_hits: number; hit_rate: number | null; avoided_at_least: number };
  unpriced: { provider: string; model: string; unit: string; rows: number; quantity: number }[];
  revenue: { by_kind: Record<string, { gross: number; vat: number; net: number }>; net: number; other_currencies: Record<string, number> };
  gross_contribution: number;
}

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

/** "tts_<code>" speaks a test phrase in that language (any supported code). */
export type TestTarget = "llm" | "stt" | `tts_${string}`;

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
    if (status === 429) return "Too many attempts, try again in a few minutes.";
    if (typeof detail === "string") return detail ? detail.charAt(0).toUpperCase() + detail.slice(1) : `Error ${status}`;
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

/** Which role an endpoint belongs to: "admin" = operator, "me" = customer. */
export type Area = "admin" | "me";

const unauthorizedHandlers: Record<Area, Set<() => void>> = { admin: new Set(), me: new Set() };

/** Register a handler for 401s of one area (e.g. redirect to that area's login). Returns an unsubscribe. */
export function onUnauthorized(area: Area, fn: () => void): () => void {
  unauthorizedHandlers[area].add(fn);
  return () => {
    unauthorizedHandlers[area].delete(fn);
  };
}

export function notifyUnauthorized(area: Area = "admin"): void {
  unauthorizedHandlers[area].forEach((fn) => fn());
}

interface RequestOpts {
  /** Do not trigger the global 401 handler (login form, status probe). */
  no401?: boolean;
  /** Area whose 401 handler is notified (default "admin"). */
  area?: Area;
}

async function errorFromResponse(res: Response, area: Area): Promise<ApiError> {
  let detail: unknown = res.statusText;
  try {
    const text = await res.text();
    if (text) {
      try {
        const data = JSON.parse(text);
        detail = data && typeof data === "object" && "detail" in data ? data.detail : data;
      } catch {
        detail = text;
      }
    }
  } catch {
    /* keep statusText */
  }
  if (res.status === 401) notifyUnauthorized(area);
  return new ApiError(res.status, detail);
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
    if (res.status === 401 && !opts.no401) notifyUnauthorized(opts.area ?? "admin");
    const detail = data && typeof data === "object" && "detail" in data ? (data as { detail: unknown }).detail : data;
    throw new ApiError(res.status, detail ?? res.statusText);
  }
  return data as T;
}

/** Verb helpers bound to one area (for 401 routing). */
function client(area: Area) {
  const o = (x?: RequestOpts): RequestOpts => ({ area, ...x });
  return {
    get: <T>(p: string, x?: RequestOpts) => request<T>("GET", p, undefined, o(x)),
    post: <T>(p: string, b?: unknown, x?: RequestOpts) => request<T>("POST", p, b ?? {}, o(x)),
    put: <T>(p: string, b: unknown) => request<T>("PUT", p, b, o()),
    patch: <T>(p: string, b: unknown) => request<T>("PATCH", p, b, o()),
    del: <T>(p: string, b?: unknown) => request<T>("DELETE", p, b, o()),
  };
}

const { get, post, put, patch, del } = client("admin");
const me = client("me");

const q = (params: Record<string, string | number | undefined | null>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  const str = s.toString();
  return str ? `?${str}` : "";
};
const enc = encodeURIComponent;

// ---------- endpoints ----------

const operatorApi = {
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
    /** All watches, or only those of one customer. */
    list: (accountId?: number) => get<Device[]>(`/api/devices${q({ account_id: accountId })}`),
    pending: () => get<PendingPairing[]>("/api/devices/pending"),
    pair: (code: string, name: string, accountId: number | null = null) =>
      post<{ device_id: string }>("/api/devices/pair", { code, name, account_id: accountId }),
    /** Move a watch to a customer (null = back to stock). The previous owner's history is erased. */
    assign: (id: string, accountId: number | null) => put<Device>(`/api/devices/${enc(id)}/account`, { account_id: accountId }),
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
  /** A short sample sentence spoken with `voice` in `language` (audio/wav). May take a few seconds. */
  voiceSample: (voice: string, language: Language) => fetchVoiceSample("/api/voice-sample", voice, language, "admin"),
  personas: {
    list: () => get<Persona[]>("/api/personas"),
    create: (p: PersonaInput) => post<Persona>("/api/personas", p),
    update: (id: number, p: PersonaInput) => put<Persona>(`/api/personas/${id}`, p),
    remove: (id: number) => del<{ ok: boolean }>(`/api/personas/${id}`),
  },
  usage: (days: number, deviceId?: string) => get<Usage>(`/api/usage${q({ days, device_id: deviceId })}`),
  finance: (days: number) => get<Finance>(`/api/finance${q({ days })}`),
  billingSettings: {
    get: () => get<BillingSettings>("/api/billing/settings"),
    update: (body: Partial<Omit<BillingSettings, "updated_at" | "updated_by" | "stripe_configured" | "stripe_mode" | "care_price_id_set">>) =>
      put<BillingSettings>("/api/billing/settings", body),
  },
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

async function fetchVoiceSample(path: string, voice: string, language: Language, area: Area): Promise<Blob> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice, language }),
    });
  } catch {
    throw new ApiError(0, "Server not reachable");
  }
  if (!res.ok) throw await errorFromResponse(res, area);
  return res.blob();
}

// ---------- operator: customer accounts (/api/accounts) ----------

const accountsApi = {
  list: (search = "", status?: AccountStatus | "") => get<AccountList>(`/api/accounts${q({ q: search, status })}`),
  get: (id: number) => get<AccountDetail>(`/api/accounts/${id}`),
  /** The customer receives an email with a link to set their password. */
  create: (body: { email: string; name?: string; country?: string | null }) => post<Account>("/api/accounts", body),
  setStatus: (id: number, status: "active" | "suspended") => patch<Account>(`/api/accounts/${id}`, { status }),
  audit: (id: number) => get<AuditEntry[]>(`/api/accounts/${id}/audit`),
  /** Complimentary/test ola Care (no payment, no Stripe objects). Idempotent unless `extend`. */
  grantComplimentary: (id: number, body: { days: number; allowance_pence?: number | null; note?: string; extend?: boolean }) =>
    post<{ created: boolean; subscription: Subscription }>(`/api/accounts/${id}/complimentary`, body),
  revokeComplimentary: (id: number) => del<{ revoked: boolean }>(`/api/accounts/${id}/complimentary`),
  /** null = back to the plan default. */
  setAllowance: (id: number, allowance_override: number | null) =>
    patch<{ allowance_override: number | null; used: number; limit: number; currency: string }>(`/api/accounts/${id}/allowance`, {
      allowance_override,
    }),
};

// ---------- operator: shop orders (/api/orders) ----------

const ordersApi = {
  list: (status?: OrderStatus | "") => get<Order[]>(`/api/orders${q({ status })}`),
  update: (id: number, body: OrderUpdate) => patch<Order>(`/api/orders/${id}`, body),
};

// ---------- customer API (/api/me) ----------

const meApi = {
  // session / sign-up (no401: a wrong password must not bounce the login form)
  get: (opts?: RequestOpts) => me.get<Account>("/api/me", opts),
  signup: (body: { email: string; password: string; name?: string; country?: string | null }) =>
    me.post<Account>("/api/me/signup", body, { no401: true }),
  login: (email: string, password: string) => me.post<Account>("/api/me/login", { email, password }, { no401: true }),
  logout: () => me.post<{ ok: boolean }>("/api/me/logout", {}, { no401: true }),
  verifyEmail: (token: string) => me.post<{ ok: boolean }>("/api/me/verify-email", { token }, { no401: true }),
  resendVerification: () => me.post<{ ok: boolean }>("/api/me/verify-email/resend"),
  forgotPassword: (email: string) => me.post<{ ok: boolean }>("/api/me/password/forgot", { email }, { no401: true }),
  /** Also sets the first password of an account created by the operator. Signs the customer in. */
  resetPassword: (token: string, password: string) =>
    me.post<Account>("/api/me/password/reset", { token, password }, { no401: true }),
  changePassword: (current_password: string, new_password: string) =>
    // no401: a wrong current password answers 401 but the session stays valid
    me.post<{ ok: boolean }>("/api/me/password/change", { current_password, new_password }, { no401: true }),
  updateProfile: (body: { name?: string; country?: string }) => me.patch<Account>("/api/me", body),
  /** Permanently erases the account, its watches' pairing and history. */
  deleteAccount: (password: string) => me.del<{ ok: boolean }>("/api/me", { password }),
  /** JSON download (Content-Disposition: attachment). */
  exportUrl: "/api/me/export",

  devices: {
    list: () => me.get<Device[]>("/api/me/devices"),
    pair: (code: string, name: string) => me.post<{ device_id: string }>("/api/me/devices/pair", { code, name }),
    rename: (id: string, name: string) => me.patch<Device>(`/api/me/devices/${enc(id)}`, { name }),
    /** Unpairs the watch and erases its history. */
    remove: (id: string) => me.del<{ ok: boolean }>(`/api/me/devices/${enc(id)}`),
    settings: (id: string) => me.get<SettingsResponse>(`/api/me/devices/${enc(id)}/settings`),
    patchSettings: (id: string, changes: SettingsPatch) =>
      me.patch<SettingsResponse>(`/api/me/devices/${enc(id)}/settings`, changes),
    conversations: (id: string) => me.get<Conversation[]>(`/api/me/devices/${enc(id)}/conversations`),
    deleteHistory: (id: string) => me.del<{ deleted_turns: number }>(`/api/me/devices/${enc(id)}/conversations`),
  },
  options: () => me.get<Options>("/api/me/options"),
  voiceSample: (voice: string, language: Language) => fetchVoiceSample("/api/me/voice-sample", voice, language, "me"),
  personas: {
    list: () => me.get<MyPersona[]>("/api/me/personas"),
    create: (p: Pick<Persona, "name" | "system_prompt">) => me.post<MyPersona>("/api/me/personas", p),
    update: (id: number, p: Pick<Persona, "name" | "system_prompt">) => me.put<MyPersona>(`/api/me/personas/${id}`, p),
    remove: (id: number) => me.del<{ ok: boolean }>(`/api/me/personas/${id}`),
  },
  items: {
    list: () => me.get<Item[]>("/api/me/items"),
    create: (body: ItemInput) => me.post<Item>("/api/me/items", body),
    update: (kind: ItemKind, number: number, body: ItemInput) => me.put<Item>(`/api/me/items/${kind}/${number}`, body),
    remove: (kind: ItemKind, number: number) => me.del<{ ok: boolean }>(`/api/me/items/${kind}/${number}`),
    setDone: (number: number, done: boolean) => me.put<Item>(`/api/me/items/reminder/${number}/done`, { done }),
  },
  usage: () => me.get<MyUsage>("/api/me/usage"),
  subscription: () => me.get<MySubscription>("/api/me/subscription"),
  plan: () => me.get<MyPlan>("/api/me/plan"),
  /** Stripe Checkout URL for ola Care (existing account). */
  subscribe: () => me.post<{ url: string }>("/api/me/subscribe"),
  /** Stripe Checkout URL for a one-off extra-usage purchase (granted only after payment). */
  topupCheckout: () => me.post<{ url: string; topup_id: number }>("/api/me/topups/checkout"),
  usageNotice: () => me.get<{ notice: UsageNotice | null }>("/api/me/usage-notice"),
  dismissUsageNotice: (threshold: number) => me.post<{ ok: boolean }>("/api/me/usage-notice/dismiss", { threshold }),
  /** Stripe Billing Portal URL (404 = no subscription on this account). */
  billingPortal: () => me.post<{ url: string }>("/api/me/billing-portal"),
};

/** Operator endpoints at the top level, customer accounts under `api.accounts`, the customer API under `api.me`. */
export const api = { ...operatorApi, accounts: accountsApi, orders: ordersApi, me: meApi };

// ---------- live events (/api/live, /api/me/live) ----------

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
  /** The server refused a question from the watch (billing / account state). */
  | ({ type: "turn_refused"; code: TurnRefusedCode } & LiveBase)
  /** Notes or reminders of the account changed (voice, another tab, a watch). */
  | { type: "items_changed"; account_id: number; at?: number }
  /** The account reached a usage threshold (80 / 95 / 100 % of the allowance). */
  | { type: "usage_threshold"; account_id: number; threshold: number; at?: number }
  | { type: "keepalive"; at?: number };

export type TurnRefusedCode = "subscription_required" | "limit_reached" | "account_inactive" | string;
