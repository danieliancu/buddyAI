/**
 * Site-wide configuration for the ola marketing site.
 *
 * Everything marked TODO(owner) must be filled in / confirmed before launch.
 * Search the project for "TODO(owner)" to find every placeholder.
 */

export type Currency = "GBP" | "EUR";

// Prices are set in pounds; euro prices are converted at GBP_TO_EUR and rounded to clean figures.
// Must match the Stripe prices (products "olawatch" and "olacare", GBP + EUR, trial length):
// STRIPE_PRICE_WATCH_GBP/EUR and STRIPE_PRICE_CARE_GBP/EUR in server/.env. After changing a price or
// the rate, create Stripe prices with the new amounts.
// Consumer prices must include VAT: configure the Stripe prices as tax-inclusive.
const PRICE_GBP = { watch: 79, care: 7.9 };

// TODO(owner): review the rate now and then (it is fixed on purpose: Stripe charges fixed euro prices).
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
  // TODO(owner): company and VAT numbers. Empty = the line is not shown.
  companyNumber: "",
  vatNumber: "",
  supportEmail: "ola@olawatch.ai",

  // TODO(owner): URL of the customer web app (sign in / "Add watch").
  appUrl: "https://app.example.com",

  // Returns: only claim free returns when this is true. TODO(owner): decide.
  freeReturns: false,

  // Shipping destinations. TODO(owner): confirm the list matches the Stripe shipping rates.
  shipping: {
    countries: [
      "United Kingdom",
      "Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czechia", "Denmark", "Estonia",
      "Finland", "France", "Germany", "Greece", "Hungary", "Ireland", "Italy", "Latvia",
      "Lithuania", "Luxembourg", "Malta", "Netherlands", "Poland", "Portugal", "Romania",
      "Slovakia", "Slovenia", "Spain", "Sweden",
    ],
    // TODO(owner): delivery times once the carrier is chosen. "TBD" renders as "to be confirmed".
    deliveryTimes: { UK: "TBD", EU: "TBD" },
    // TODO(owner): shipping cost wording once the Stripe shipping rates exist.
    costNote: "Shipping cost is shown at checkout before you pay.",
  },

  // Cookieless analytics (Plausible). Empty string = analytics off.
  // TODO(owner): set e.g. "www.example.com" to enable.
  plausibleDomain: "",
  plausibleSrc: "https://plausible.io/js/script.js",

  // Optional newsletter form endpoint. Empty = the footer shows a "sales open soon" note
  // instead of an email field (nothing is collected). TODO(owner): add a provider if wanted.
  newsletterAction: "",

  // Social profiles shown in the footer (only rendered when non-empty).
  // TODO(owner): add real profiles, e.g. { label: "Instagram", href: "https://..." }.
  social: [] as { label: string; href: string }[],
};

export function formatPrice(amount: number, currency: Currency): string {
  const symbol = currency === "GBP" ? "£" : "€";
  return `${symbol}${amount.toFixed(2)}`;
}

export function deliveryText(value: string): string {
  return value === "TBD" ? "to be confirmed" : value;
}
