// Client-side currency preference (GBP default; EUR for EU locales). Shared by the price islands.
export type Cur = "GBP" | "EUR";

const KEY = "buddyai.currency";
const EU_REGIONS = new Set(
  "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split(" "),
);
const EU_LANGS = new Set("de fr it es nl fi el sv da pl cs sk sl hu ro bg hr et lv lt mt ga lb".split(" "));

function detect(): Cur | null {
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
