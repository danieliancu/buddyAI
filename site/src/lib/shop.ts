// Shop status from the ola server (/api/shop/status), fetched once per page and shared by the buy box and the
// home page's structured data. Any error counts as closed.
export interface ShopStatus {
  open: boolean;
  /** Currencies checkout is configured for (lower case: "gbp", "eur"). */
  currencies: string[];
}

let pending: Promise<ShopStatus> | null = null;

export function shopStatus(): Promise<ShopStatus> {
  pending ??= fetch("/api/shop/status", { headers: { Accept: "application/json" }, cache: "no-store" })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then((s: { open?: boolean; currencies?: unknown }) => ({
      open: Boolean(s && s.open),
      currencies: Array.isArray(s?.currencies) ? s.currencies.map((c) => String(c).toLowerCase()) : [],
    }))
    .catch(() => ({ open: false, currencies: [] }));
  return pending;
}

/** Can checkout be opened in this currency right now? */
export function canBuyIn(s: ShopStatus, currency: string): boolean {
  return s.open && s.currencies.includes(currency.toLowerCase());
}
