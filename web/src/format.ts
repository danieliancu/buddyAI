// Formatting helpers (English UI; dates in en-GB style, e.g. 28 Sept, 13:34).

/** Backend datetimes are UTC; SQLite may drop the offset, so treat naive values as UTC. */
export function parseDate(s: string | null | undefined): Date | null {
  if (!s) return null;
  const hasTz = /[zZ]|[+-]\d\d:?\d\d$/.test(s);
  const d = new Date(hasTz ? s : `${s}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

const dtFmt = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});
const dFmt = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" });
const tFmt = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export const fmtDateTime = (s: string | null | undefined) => {
  const d = parseDate(s);
  return d ? dtFmt.format(d) : "—";
};
export const fmtDate = (s: string | null | undefined) => {
  const d = parseDate(s);
  return d ? dFmt.format(d) : "—";
};
export const fmtTime = (s: string | null | undefined) => {
  const d = parseDate(s);
  return d ? tFmt.format(d) : "—";
};

export function fmtAgo(s: string | null | undefined, now = Date.now()): string {
  const d = parseDate(s);
  if (!d) return "never";
  const sec = Math.max(0, Math.round((now - d.getTime()) / 1000));
  if (sec < 45) return "just now";
  const min = Math.round(sec / 60);
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  if (h < 24) return `${h} h ago`;
  const days = Math.round(h / 24);
  if (days < 30) return days === 1 ? "yesterday" : `${days} days ago`;
  return fmtDate(s);
}

export const fmtMs = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v).toLocaleString("en-US")} ms`);

/** Money in any currency; more decimals for tiny amounts (AI costs are often fractions of a penny). */
export function fmtMoney(v: number | null | undefined, currency = "GBP"): string {
  if (v == null) return "—";
  const abs = Math.abs(v);
  const digits = abs === 0 ? 2 : abs >= 1 ? 2 : abs >= 0.01 ? 4 : 6;
  try {
    return new Intl.NumberFormat("en-GB", { style: "currency", currency, minimumFractionDigits: 2, maximumFractionDigits: digits }).format(v);
  } catch {
    return `${v.toFixed(digits)} ${currency}`;
  }
}

export function fmtUsd(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v === 0) return "$0";
  const abs = Math.abs(v);
  const digits = abs >= 1 ? 2 : abs >= 0.01 ? 4 : 6;
  return `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: digits })}`;
}

export function fmtQty(v: number): string {
  return v.toLocaleString("en-US", { maximumFractionDigits: v < 10 ? 2 : 0 });
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export const UNIT_LABEL: Record<string, string> = {
  audio_second: "audio seconds",
  input_token: "input tokens",
  output_token: "output tokens",
  character: "characters",
};

export const KIND_LABEL: Record<string, string> = { stt: "STT", llm: "LLM", tts: "TTS" };

export const STATE_LABEL: Record<string, string> = {
  idle: "Idle",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
};

export const STATUS_LABEL: Record<string, string> = {
  completed: "completed",
  aborted: "aborted",
  error: "error",
  no_speech: "no speech",
  active: "in progress",
};

export const LANG_LABEL: Record<string, string> = { ro: "Romanian", en: "English" };
