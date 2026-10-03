// Shop status from the ola server (/api/shop/status), fetched once per page and shared by the header and buy box.
export interface ShopStatus {
  open: boolean;
}

let pending: Promise<ShopStatus> | null = null;

export function shopStatus(): Promise<ShopStatus> {
  pending ??= fetch("/api/shop/status", { headers: { Accept: "application/json" }, cache: "no-store" })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then((s: { open?: boolean }) => ({ open: Boolean(s && s.open) }))
    .catch(() => ({ open: false }));
  return pending;
}
