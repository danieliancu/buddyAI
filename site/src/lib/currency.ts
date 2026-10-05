// Client-side currency, chosen automatically (there is no switch): GBP for visitors in the UK, EUR for
// everyone else. By the device's time zone, then the browser language's region. Shared by the price islands.
export type Cur = "GBP" | "EUR";

// The UK and the Crown dependencies pay in pounds.
const UK_ZONES = new Set("Europe/London Europe/Belfast Europe/Isle_of_Man Europe/Jersey Europe/Guernsey GB GB-Eire".split(" "));

/** GBP in a UK time zone, EUR in any other, null when the time zone is unknown. */
function detectZone(): Cur | null {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    if (!tz) return null;
    return UK_ZONES.has(tz) ? "GBP" : "EUR";
  } catch {
    return null; /* Intl unavailable */
  }
}

/** GBP when the browser language is British English (en-GB), else EUR. */
function detectLanguage(): Cur {
  try {
    const tag = (navigator.languages?.[0] || navigator.language || "").trim();
    const region = tag.split(/[-_]/).slice(1).find((p) => /^[A-Za-z]{2}$/.test(p))?.toUpperCase();
    return region === "GB" ? "GBP" : "EUR";
  } catch {
    return "EUR";
  }
}

let cached: Cur | null = null;

export function getCurrency(): Cur {
  return (cached ??= detectZone() ?? detectLanguage());
}

/** Show every price on the page in the given currency. */
export function applyCurrency(c: Cur): void {
  document.documentElement.dataset.currency = c;
  document.querySelectorAll<HTMLElement>("[data-money]").forEach((el) => {
    const v = c === "EUR" ? el.dataset.eur : el.dataset.gbp;
    if (v) el.textContent = v;
  });
  document.dispatchEvent(new CustomEvent("buddyai:currency", { detail: c }));
}
