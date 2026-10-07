// The thank-you page after Stripe Checkout. Reaching it proves nothing: the page asks the server, which only
// knows what Stripe's verified webhooks reported (GET /api/shop/checkout-status), and says what is true.

export type CheckoutState = "processing" | "paid" | "failed" | "cancelled" | "unknown";

export interface CheckoutStatus {
  state: CheckoutState;
  needs_password: boolean;
}

export interface ThankYouView {
  tone: "wait" | "ok" | "warn" | "error";
  title: string;
  body: string;
  /** Show the next steps (password, phone, Wi-Fi, pairing, trial). */
  showNext: boolean;
  /** Keep asking the server. */
  poll: boolean;
}

/** What the page says for a status. `timedOut`: we stopped polling while still processing. */
export function viewFor(status: CheckoutStatus | null, opts: { timedOut?: boolean; missingSession?: boolean } = {}): ThankYouView {
  if (opts.missingSession) {
    return {
      tone: "warn",
      title: "We couldn't find your checkout",
      body: "If you completed a payment, your confirmation email is on its way. You can also sign in to your ola account to see your order.",
      showNext: false,
      poll: false,
    };
  }
  if (!status) {
    return { tone: "wait", title: "Confirming your payment…", body: "This usually takes a few seconds.", showNext: false, poll: true };
  }
  switch (status.state) {
    case "paid":
      return {
        tone: "ok",
        title: "Payment confirmed — thank you!",
        body: status.needs_password
          ? "Your order is placed. We've emailed you a link to set your ola account password."
          : "Your order is placed. Sign in to your ola account to follow it and set up your watch.",
        showNext: true,
        poll: false,
      };
    case "failed":
      return {
        tone: "error",
        title: "The payment didn't go through",
        body: "Nothing was charged and no order was placed. You can try again with another payment method.",
        showNext: false,
        poll: false,
      };
    case "cancelled":
      return { tone: "warn", title: "Checkout cancelled", body: "No order was placed and nothing was charged.", showNext: false, poll: false };
    case "processing":
    case "unknown":
    default:
      return opts.timedOut
        ? {
            tone: "wait",
            title: "Your payment is still being confirmed",
            body: "Some payment methods take longer. We'll email you as soon as it's confirmed — you don't need to keep this page open.",
            showNext: false,
            poll: false,
          }
        : { tone: "wait", title: "Confirming your payment…", body: "This usually takes a few seconds.", showNext: false, poll: true };
  }
}

/** Checkout session ids from Stripe look like cs_test_… / cs_live_…; anything else is ignored. */
export function sessionIdFrom(search: string): string | null {
  const id = new URLSearchParams(search).get("session_id") ?? "";
  return /^cs_[A-Za-z0-9_]{6,250}$/.test(id) ? id : null;
}
