// Client-side currency. The launch market is Great Britain, so every price is shown in pounds.
export type Cur = "GBP" | "EUR";

export function getCurrency(): Cur {
  return "GBP";
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
