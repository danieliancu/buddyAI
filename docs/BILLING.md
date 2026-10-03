# ola Care — billing, allowance and fair use

Operator and developer reference. Customer-facing wording lives in the web app (Account → ola Care → "How usage works").

## Money model

| Setting (operator: Usage → Plan settings) | Default | Meaning |
|---|---|---|
| Care selling price | £7.90 / month | What the customer pays. Stripe price id `BUDDYAI_STRIPE_PRICE_CARE_GBP` must match. |
| Included AI allowance | £2.50 / period | Internal provider-cost budget per account (never shown in £ to customers). |
| Extra usage price | £1.99 one-off | Stripe Checkout `mode=payment`, `price_data` from settings. Never recurring. |
| Extra usage allowance | £0.65 | Added to the current period only. |
| Notification thresholds | 80, 95, 100 % | Each shown once per period (web banner/dialog, watch notice, first one also by email). |
| USD → GBP rate | 0.75 | Applied when usage is recorded; recorded rows keep their rate. |
| Reserve per running turn | 3p | Concurrency guard (see below). |
| Enforce | off | Check subscriptions/allowances without Stripe keys. Always on while Stripe is configured. |

These are **proposals, not commercially approved prices**. All changes are written to the audit log.

## Allowance

- **Per account**, shared by all its watches.
- **Period**:
  - Stripe subscriptions use their billing period.
  - Complimentary grants use 30-day cycles from the grant start.
  - Otherwise, the calendar month.
- **Used**: the sum of `usage_records.cost_micro_gbp` in the period, counting only rows that are `billable` and not `mock`.
  - Amounts are integer millionths of £1.
  - The GBP cost is frozen at write time, so a later exchange-rate change does not rewrite past usage.
- **Unpriced usage** (no pricing rule) cannot be counted. It is flagged in the operator Finance tab and on the account, never treated as £0.
- **Not charged to customers**: turns that fail on our side (`turn_status=error`). Their cost stays visible to the operator.
- **Charged**: aborted and no-speech turns, for the stages that ran.
- **Limit**: plan allowance (or the per-account override, or the grant's own allowance), plus paid top-ups of the period.
- **Customers see a percentage** (floored: 100 % means really used up), activity count, reset date, prices and top-ups. They never see £ costs, tokens or providers.

### Concurrency

The check and the reservation run under one lock per account (`app/entitlements.py`).
- A single watch is refused only at 100 %.
- While other turns of the account are running, a new one needs room for all of their reservations plus its own. This stops several watches from starting turns past the limit together.
- A turn that has started always finishes, so the last answer may overshoot slightly.
- The state lives in process memory. **Running more than one server worker needs a shared store** (for example Redis or row locks) before production scale-out.

## Subscription states

| Source | State | Access |
|---|---|---|
| Stripe | trialing, active, past_due | yes (past_due: Stripe is retrying the card) |
| Stripe | canceled, unpaid, incomplete… | no → `subscription_required` |
| Complimentary | active and `now < current_period_end` | yes |
| Complimentary | expired / revoked | no ("Pilot ended") |
| Internal account (`accounts.internal`) | — | always, costs still tracked |

## Complimentary / test access

- **Where to grant it:**
  - Operator UI: Customers → account → *Grant ola Care*, with *Extend* and *End now*.
  - CLI: `python tools/grant_pilot.py --account-id <id> --expect-device <watch name> [--days 30] [--dry-run] [--enable-enforcement]`
- **Idempotent**: an unexpired grant is returned unchanged unless it is extended. The grant refuses accounts that pay through Stripe.
- **No Stripe objects** are created and no ids are fabricated. The grant is recorded as `subscriptions.source = "complimentary"`, with the note and the operator who granted it.
- **Expiry**: after `current_period_end` the account shows "Pilot ended" and the watch receives `subscription_required`, until the grant is extended or the customer subscribes.

## Top-ups

1. `POST /api/me/topups/checkout` creates a `pending` TopUp row and a Checkout Session. The row's metadata holds `topup_id` and `account_id`.
2. A webhook grants it only if all of these hold:
   - the event is `checkout.session.completed` or `…async_payment_succeeded`;
   - `payment_status == "paid"`;
   - the metadata matches the row's account;
   - the amount is at least the price and the currency is GBP;
   - an atomic `UPDATE … WHERE status='pending'` succeeds.

   Duplicate or concurrent deliveries therefore grant once.
3. Failures and cancellations:
   - Expired or failed sessions are marked `expired` and grant nothing.
   - Abandoned checkouts stay `pending` and grant nothing.
4. **Refund policy**: `charge.refunded` marks the top-up `refunded` and withdraws its extra allowance for the period, even if part of it was used. A negative revenue event is recorded. The customer-facing text says so.

## Webhooks

- **Idempotency**: each event id is *claimed* first (a unique `stripe_events` row). A duplicate claim is a no-op. A handler exception releases the claim so Stripe's retry can apply the event.
- **Revenue**: stored gross, with VAT kept apart, for the operator's net revenue and gross contribution. It is recorded from:
  - `invoice.paid` (subscriptions, £0 trial invoices skipped)
  - paid top-ups
  - watch orders
  - refunds (negative)

## Operator finance (Usage → Finance)

The Finance tab shows:
- provider cost by component (STT, LLM input / cached input / output, web search, TTS);
- cost per customer and per watch, and daily and monthly trends;
- cost per completed interaction (mean, p50, p90, p95);
- the cost of aborted and failed interactions;
- searches, cache hits, hit rate and avoided search fees;
- missing pricing rules;
- net revenue by source, and gross contribution (net revenue minus provider cost).

Gross contribution comes **before** hardware, hosting, payment fees, support and other operating expenses; it is not profit. All costs are estimates (usage × pricing rules), not provider invoices, and mock usage is excluded.

## Production checklist (not done — needs explicit authorisation)

- [ ] Approve the prices commercially: Care, extra usage, allowances and thresholds.
- [ ] Create the Stripe products and prices in **test** mode, set `BUDDYAI_STRIPE_*` test keys and the webhook secret, and run the checkout, top-up and refund journey with the Stripe CLI (`stripe listen --forward-to …/api/stripe/webhook`).
- [ ] Webhook events to enable:
  - `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `checkout.session.expired`
  - `customer.subscription.*`
  - `invoice.paid`, `invoice.payment_failed`
  - `charge.refunded`
- [ ] Stripe Tax and VAT registration. The extra-usage price is VAT-inclusive (`tax_behavior=inclusive`).
- [ ] Have consumer-law and fair-use wording reviewed (UK CMA / subscription rules): no "unlimited" claims, a clear renewal/reset date and refund wording.
- [ ] Before running more than one server worker, move the concurrency reservations to a shared store.
- [ ] Live keys (`sk_live_…`) only after all of the above.
