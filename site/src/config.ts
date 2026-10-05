/**
 * Site-wide configuration for the ola marketing site: prices, company details, shipping destinations.
 * Pages, structured data and llms.txt all read from here, so the figures stay consistent.
 */

export type Currency = "GBP" | "EUR";

// Prices are set in pounds; euro prices are converted at GBP_TO_EUR and rounded to clean figures.
// Must match the Stripe prices (products "olawatch" and "olacare", GBP + EUR, trial length):
// STRIPE_PRICE_WATCH_GBP/EUR and STRIPE_PRICE_CARE_GBP/EUR in server/.env. After changing a price or
// the rate, create Stripe prices with the new amounts.
// Consumer prices must include VAT: configure the Stripe prices as tax-inclusive.
const PRICE_GBP = { watch: 79, care: 7.9 };

// Review the rate now and then (it is fixed on purpose: Stripe charges fixed euro prices).
export const GBP_TO_EUR = 1.17;

/** A converted price rounded to a clean figure: whole euros from €20 up, else to the nearest €0.50. */
export function toEur(gbp: number): number {
  const eur = gbp * GBP_TO_EUR;
  return eur >= 20 ? Math.round(eur) : Math.round(eur * 2) / 2;
}

export const prices = {
  watch: { GBP: PRICE_GBP.watch, EUR: toEur(PRICE_GBP.watch) },
  care: { GBP: PRICE_GBP.care, EUR: toEur(PRICE_GBP.care) },
  trialMonths: 3,
  currencyDefault: "GBP" as Currency,
} as const;

export const config = {
  siteName: "olawatch",
  tagline: "Your AI companion, now on your wrist.",

  // Company details shown in the footer, contact page and legal pages.
  companyName: "Ola Technologies London Ltd",
  address: "Essex, United Kingdom",
  // Company and VAT numbers. Empty = the line is not shown.
  companyNumber: "",
  vatNumber: "",
  supportEmail: "ola@olawatch.ai",

  // The customer web app (sign in, "Add watch", notes, reminders, ola Care).
  appUrl: "https://app.olawatch.ai",

  // Returns: only claim free returns when this is true.
  freeReturns: false,

  // Delivery: rates and times come from Stripe at checkout (BUDDYAI_STRIPE_SHIPPING_RATES_*), never from here.
  shipping: {
    note: "Delivery options, shipping costs and estimated delivery times are shown at checkout before you pay.",
  },

  // Cookieless analytics (Plausible). Empty string = analytics off; set the site's domain to enable.
  plausibleDomain: "",
  plausibleSrc: "https://plausible.io/js/script.js",

  // Optional newsletter form endpoint. Empty = the footer shows a "sales open soon" note
  // instead of an email field (nothing is collected).
  newsletterAction: "",

  // Social profiles shown in the footer (only rendered when non-empty).
  // e.g. { label: "Instagram", href: "https://..." }.
  social: [] as { label: string; href: string }[],
};

export function formatPrice(amount: number, currency: Currency): string {
  const symbol = currency === "GBP" ? "£" : "€";
  return `${symbol}${amount.toFixed(2)}`;
}

/** Date of the last content update of the legal pages (set by hand when their text changes). */
export const legalLastUpdated = "2026-10-05";

export function formatDate(iso: string): string {
  return new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}

/**
 * Where we ship, and where the returns policy applies: the UK and the 27 EU member states (ISO 3166-1 alpha-2).
 * Keep in step with BUDDYAI_SHIP_COUNTRIES (server/app/config.py), the Stripe checkout's allowed countries.
 */
export const shipCountries = [
  ["GB", "United Kingdom"],
  ["AT", "Austria"], ["BE", "Belgium"], ["BG", "Bulgaria"], ["HR", "Croatia"], ["CY", "Cyprus"],
  ["CZ", "Czechia"], ["DK", "Denmark"], ["EE", "Estonia"], ["FI", "Finland"], ["FR", "France"],
  ["DE", "Germany"], ["GR", "Greece"], ["HU", "Hungary"], ["IE", "Ireland"], ["IT", "Italy"],
  ["LV", "Latvia"], ["LT", "Lithuania"], ["LU", "Luxembourg"], ["MT", "Malta"], ["NL", "Netherlands"],
  ["PL", "Poland"], ["PT", "Portugal"], ["RO", "Romania"], ["SK", "Slovakia"], ["SI", "Slovenia"],
  ["ES", "Spain"], ["SE", "Sweden"],
] as const;
export const shipCountryCodes: string[] = shipCountries.map(([code]) => code);
export const shipCountryNames: string[] = shipCountries.map(([, name]) => name);
