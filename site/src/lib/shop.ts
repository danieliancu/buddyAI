// Shop status from the ola server (/api/shop/status), fetched once per page and shared by the buy box and the
// home page's structured data. Any error counts as closed.

/** The ola Care terms the buyer must accept before checkout, exactly as the server will record them. */
export interface CareTerms {
  version: string;
  sha256: string;
  text: string;
  amount_minor: number;
  interval: string;
}

export interface ShopStatus {
  open: boolean;
  /** Currencies checkout is configured for (lower case: "gbp", "eur"). */
  currencies: string[];
  trialDays?: number;
  /** By currency (lower case). A currency without terms cannot be bought. */
  careTerms?: Record<string, CareTerms>;
}

let pending: Promise<ShopStatus> | null = null;

export function parseShopStatus(s: unknown): ShopStatus {
  const o = (s && typeof s === "object" ? s : {}) as Record<string, unknown>;
  const terms: Record<string, CareTerms> = {};
  const raw = o.care_terms && typeof o.care_terms === "object" ? (o.care_terms as Record<string, Partial<CareTerms>>) : {};
  for (const [cur, t] of Object.entries(raw)) {
    if (t && typeof t.text === "string" && typeof t.version === "string" && typeof t.sha256 === "string") {
      terms[cur.toLowerCase()] = {
        version: t.version,
        sha256: t.sha256,
        text: t.text,
        amount_minor: Number(t.amount_minor) || 0,
        interval: String(t.interval || "month"),
      };
    }
  }
  return {
    open: Boolean(o.open),
    currencies: Array.isArray(o.currencies) ? o.currencies.map((c) => String(c).toLowerCase()) : [],
    trialDays: typeof o.trial_days === "number" ? o.trial_days : undefined,
    careTerms: terms,
  };
}

export function shopStatus(): Promise<ShopStatus> {
  pending ??= fetch("/api/shop/status", { headers: { Accept: "application/json" }, cache: "no-store" })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then(parseShopStatus)
    .catch(() => ({ open: false, currencies: [] }));
  return pending;
}

/** Can checkout be opened in this currency right now (configured, and its Care terms are known)? */
export function canBuyIn(s: ShopStatus, currency: string): boolean {
  const c = currency.toLowerCase();
  return s.open && s.currencies.includes(c) && !!s.careTerms?.[c];
}

/** The checkout request: the currency and proof of the exact terms the buyer ticked. */
export function checkoutBody(currency: string, terms: CareTerms, accepted: boolean) {
  return {
    currency: currency.toLowerCase(),
    care_terms_accepted: accepted,
    care_terms_version: terms.version,
    care_terms_sha256: terms.sha256,
  };
}
