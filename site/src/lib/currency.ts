// Client-side currency: GBP in the UK, EUR in the rest of Europe (by time zone, then browser language). Shared by the price islands.
export type Cur = "GBP" | "EUR";

const KEY = "buddyai.currency";
const EU_REGIONS = new Set(
  "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split(" "),
);
const EU_LANGS = new Set("de fr it es nl fi el sv da pl cs sk sl hu ro bg hr et lv lt mt ga lb".split(" "));

// Where the visitor is, from the device's time zone: the UK (and the Crown dependencies) pay in pounds,
// the rest of Europe in euros. EU territories outside the "Europe/" zones are listed too.
const UK_ZONES = new Set("Europe/London Europe/Belfast Europe/Isle_of_Man Europe/Jersey Europe/Guernsey GB".split(" "));
const EUR_ZONES_OUTSIDE_EUROPE = new Set(
  "Atlantic/Canary Atlantic/Madeira Atlantic/Azores Africa/Ceuta Asia/Nicosia Asia/Famagusta".split(" "),
);

/** GBP in the UK, EUR anywhere else in Europe, null outside Europe (then the browser language decides). */
function detectZone(): Cur | null {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    if (UK_ZONES.has(tz)) return "GBP";
    if (tz.startsWith("Europe/") || EUR_ZONES_OUTSIDE_EUROPE.has(tz)) return "EUR";
  } catch {
    /* Intl unavailable */
  }
  return null;
}

function detect(): Cur | null {
  return detectZone() ?? detectLanguage();
}

function detectLanguage(): Cur | null {
  try {
    const tag = (navigator.languages?.[0] || navigator.language || "").trim();
    if (!tag) return null;
    const parts = tag.split(/[-_]/);
    const lang = parts[0].toLowerCase();
    const region = parts.slice(1).find((p) => /^[A-Za-z]{2}$/.test(p))?.toUpperCase();
    if (region) return EU_REGIONS.has(region) ? "EUR" : null;
    return EU_LANGS.has(lang) ? "EUR" : null;
  } catch {
    return null;
  }
}

export function getCurrency(fallback: Cur = "GBP"): Cur {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "GBP" || saved === "EUR") return saved;
  } catch {
    /* storage unavailable */
  }
  return detect() ?? fallback;
}

export function setCurrency(c: Cur): void {
  try {
    localStorage.setItem(KEY, c);
  } catch {
    /* storage unavailable: the choice lasts for this page only */
  }
  applyCurrency(c);
}

/** Update every price on the page and every toggle's pressed state. */
export function applyCurrency(c: Cur): void {
  document.documentElement.dataset.currency = c;
  document.querySelectorAll<HTMLElement>("[data-money]").forEach((el) => {
    const v = c === "EUR" ? el.dataset.eur : el.dataset.gbp;
    if (v) el.textContent = v;
  });
  document.querySelectorAll<HTMLButtonElement>("[data-currency-btn]").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.currencyBtn === c));
  });
  document.dispatchEvent(new CustomEvent("buddyai:currency", { detail: c }));
}
