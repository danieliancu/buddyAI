/**
 * Site-wide configuration for the ola marketing site.
 *
 * Everything marked TODO(owner) must be filled in / confirmed before launch.
 * Search the project for "TODO(owner)" to find every placeholder.
 */

export type Currency = "GBP" | "EUR";

// TODO(owner): must match the Stripe prices (products "ola Watch" and "ola Care", GBP + EUR, trial length).
// Consumer prices must include VAT: configure the Stripe prices as tax-inclusive.
export const prices = {
  watch: { GBP: 199, EUR: 229 },
  care: { GBP: 4.99, EUR: 5.99 },
  trialMonths: 3,
  currencyDefault: "GBP" as Currency,
} as const;

export const config = {
  siteName: "ola",
  tagline: "Your AI companion, now on your wrist.",

  // TODO(owner): company details shown in the footer, contact page and legal pages.
  companyName: "TODO(owner): Company Name Ltd",
  address: "TODO(owner): Registered office address, City, Postcode, United Kingdom",
  companyNumber: "TODO(owner): Company number",
  vatNumber: "TODO(owner): VAT number",
  // TODO(owner): real support mailbox (used for mailto: links).
  supportEmail: "support@example.com",

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
  const fixed = Number.isInteger(amount) ? String(amount) : amount.toFixed(2);
  return `${symbol}${fixed}`;
}

export function deliveryText(value: string): string {
  return value === "TBD" ? "to be confirmed" : value;
}
