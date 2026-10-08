# ola Care — billing, AI interactions and cost monitoring

Operator and developer reference. Customer-facing wording lives in the web app (Account → ola Care → "How
interactions work"), on the site (pricing, FAQ, subscription terms §3a) and in the consent text (`app/care_terms.py`).

Two separate systems:
- **A. The customer's interaction allowance — enforced.** ola Care includes **1,000 AI interactions per billing
  period**, shared by the account's watches. This is the only usage limit customers have.
- **B. Internal AI cost monitoring — never enforced.** What the AI providers cost us per account and period is
  recorded and compared with an internal target (£2.50) for operator alerts and profitability views. It never
  refuses, slows or degrades a customer's request.

## Plan settings

| Setting (operator: Usage → Plan settings) | Default | Meaning |
|---|---|---|
| Care selling price | £7.99 / month | What the customer pays. Stripe price id `BUDDYAI_STRIPE_PRICE_CARE_GBP` must match. |
| AI interactions per month | 1,000 | The customer's allowance per billing period, shared by all the account's watches. Enforced. |
| Extra usage price | £1.99 one-off | Stripe Checkout `mode=payment`, `price_data` from settings. Never recurring. |
| Extra usage: interactions added | 250 | Added to the current period only. |
| Notification thresholds | 80, 95, 100 % | Of the interactions; each shown once per period (web banner/dialog, watch notice, first one also by email). |
| Cost: early warning / target / critical | £2.00 / £2.50 / £5.00 | Internal monitoring per account and period (operator only, never enforced). |
| USD → GBP rate | 0.75 | Applied when usage is recorded; recorded rows keep their rate. |
| Enforce | off | Check subscriptions and interactions without Stripe keys. Always on while Stripe is configured. |

A per-account **interaction override** (Customers → account → Plan & allowance) is an authorised exception.
The pre-0027 money allowance settings (`care_allowance_pence`, `topup_allowance_pence`, `reserve_pence`,
`accounts.allowance_override`, a complimentary grant's `allowance_pence`) are kept in the database for history
and are no longer read. All setting changes are written to the audit log.

## Interactions (the customer's allowance)

- **Per account**, shared by all its watches; **per billing period**:
  - Stripe subscriptions use their billing period. A Stripe period longer than a month — a free trial longer than
    a month, whose Stripe period is the whole trial — is split into monthly cycles from its start
    (`allowance.period_for`), so the 1,000 renew every month during the trial too; a trial of one month or less
    is one cycle.
  - Complimentary grants use 30-day cycles from the grant start.
  - Otherwise, the calendar month.
- **Nothing rolls over**: counts are per period key.

**Counting rule** (`app/interactions.py`, decided once at settlement, stored in `usage_operations.interaction`):

| Counts as one interaction | Never counts |
|---|---|
| A chat / note / reminder request that was understood (a transcript exists) and completed | Silence (`no_speech`), nothing understood |
| The same, cancelled by the user (tap) after it was understood | Failures on our side: provider / server error, `start_failed`, the watch gave up waiting (`timeout`), lease lost, shutdown, expired operations |
| | In a note / reminder edit screen (mic left open): talk not meant for the item (`ignored`) or about another item (`other`) |
| One request = one interaction, however many AI calls, web searches or tools it used | A dropped connection (`connection_lost`) or a watch-side `error` abort |
| A follow-up is a new request | Refused or duplicate requests (a resend never runs twice) |
| | Background work: memory learning, embeddings; voice samples; operator tests |

Operations from before migration 0027 have `interaction = NULL`: they were never counted and nothing is
reconstructed. Migration 0027 also fixes the size of top-ups already bought (250 interactions) and deletes the
usage notices recorded under the money allowance (they described money; the 80/95/100 % notices then follow the
interaction count from the first period). For the period running at deploy the operator projection is marked
*incomplete* (it holds costs of requests that were never counted). **Limit** = the plan's interactions (or the account's override) + the interactions of the period's
paid top-ups. **Customers see** used, limit, remaining, % used (floored), the renewal date and their top-ups —
never costs, tokens or providers.

### Usage operations (multi-process safe)

Every AI-consuming operation is one row in `usage_operations` (`app/usage_ops.py`). **PostgreSQL is the
source of truth**: any number of server processes or hosts share one interaction allowance per account. Nothing
about admission lives in process memory.

**States**: `reserved` → `running` → one of `settled` / `cancelled` (nothing was spent) / `expired` (the lease
ran out). A terminal operation is never revived. Separately, **cost certainty**: `pending`, `exact`,
`unpriced` (a call had no pricing rule) or `uncertain` (expired while running: a provider may still report usage).

**Admission** (`admit`): one short transaction that
1. locks the account row (`SELECT … FOR UPDATE`; SQLite in development: `BEGIN IMMEDIATE`, the database write lock),
2. looks up the request (`r:<request_id>` from the watch, or `l:<device>:<session>:<turn>` for older firmware):
   a known request never creates a second operation,
3. applies the rules below and inserts the operation with a lease (`usage_lease_s`, 90 s), the owner process
   (`host:pid:boot`) and an execution token.

If the database cannot decide (down, timeout), the turn is refused (`service_unavailable`), never allowed.

**Rules** for a customer request (chat / note / reminder) on an enforced account. Notation: `used` = operations
of the period settled as an interaction; `active` = requests of the period still running; `limit` as above.

| Condition | Result |
|---|---|
| `used ≥ limit` | `limit_reached` (the 1,000th is admitted, the 1,001st refused) |
| `used + active ≥ limit` | `busy_concurrent`: "other conversations are in progress, try again when they finish" |
| otherwise | admitted (`reserved_micro = 0`: an admitted request holds one interaction while it runs) |

So concurrent watches and server processes can never take an account past its limit. Memory learning
(`kind=memory`) needs an entitled subscription but never uses or checks the interaction limit. Internal accounts,
unowned watches (operator stock) and unenforced plans are admitted without any check (costs still recorded).
**AI cost never enters admission.**

**Customers see settled interactions only**: never a running request.

**Lease and heartbeat**: the process running the turn extends the lease every `usage_heartbeat_s` (20 s), and
writes the costs reported so far at the same time. Only the token holder can extend it, and only while it is
active and not yet expired. If it cannot (the lease expired, or the database stayed unreachable until it would
have), the turn is stopped: no new paid stage starts. The lease uses the database clock (`now()`).

**Costs**: written as the turn goes (heartbeat) and at settlement, in integer micro-GBP. Each provider call has a
dedup key `op_uid:index:kind:provider:model:unit`, unique in `usage_records`. A repeat is ignored, and a larger
quantity for the same key replaces the smaller one, because live counters only grow. Every row carries the
operation's `period_key`. A cost that arrives after the operation ended (or after the period rolled over)
stays in the operation's period, with the operation's billable flag, and is logged as `late_cost`. Speech-to-text
audio already sent is recorded even when the turn is cancelled while listening.

**Settlement** (`settle`, idempotent): records whether the operation **counts as an interaction** (the rule
above; a second settle never counts again), reconciles the last costs and sets the operation's `billable` flag on
its rows. `billable` is now a **finance** attribute only (costs that belong to the customer's consumption, versus
failures on our side); it does not limit anything. If the database is briefly down, settlements wait in a per-process retry queue. If the process dies
first, recovery expires the operation.

**Recovery** (`recover_expired`, every `usage_recovery_interval_s` in **every** process): operations whose lease
ran out become `expired`, with reason `lease_expired`. Their costs stay visible to the operator; they are **not
billable and never count as an interaction** (owner decision). Certainty is `exact` when nothing can have been spent (still `reserved`, no costs),
otherwise `uncertain`. On PostgreSQL each process takes the account with `SKIP LOCKED` and changes the state
conditionally, so one operation is recovered exactly once. Nothing is ever executed again.

**Lock order**, everywhere (admission, settlement, recovery, Stripe webhooks, top-ups, refunds, grants,
overrides, suspension/deletion): account row → subscriptions / top-ups → `usage_operations` (ascending id) →
`usage_records` / `usage_notices`. The shared helper is `app/account_lock.py`.

**Period keys** (`app/allowance.py period_identity`) are stable:
- `stripe:<subscription id>:<period start epoch>`; while a renewal webhook is late, the next monthly period is
  predicted forward from the old end, so it gets the same key the real one will have;
- `comp:<grant id>:<cycle start epoch>`;
- `cal:<YYYY-MM>`.

Operations, their costs and new top-ups store the key. Rows written before this change have no key and are
counted by their time window.

**Threshold notices** stay unique per account, period and threshold (unique constraint): never sent twice,
whichever process evaluates them.

**Non-turn AI calls**: customer voice samples are free (owner decision) and rate-limited (30 per hour per
account); their cost is recorded as a non-billable `voice_sample` operation. The operator's voice samples and
provider tests (`/api/system/test/*`) are a documented exception (`operator_test`, recorded, non-billable).
`tests/test_ai_call_sites.py` fails if a new provider call site appears without admission or a documented
exception.

**Money is not capped — by design.** A customer may use all their interactions whatever they cost us. Provider
spend is monitored (below), never limited by admission. A turn that has started always finishes.

## Internal AI cost monitoring (operator only, never enforced)

`app/cost_monitor.py`. Per account and current billing period:
- **actual AI cost**: every priced, non-mock provider cost of the period (micro-GBP), split into interactive (the
  customer's requests) and background (memory learning, embeddings, voice samples), and by component (STT, LLM,
  TTS, web search, embeddings);
- **average per interaction** = total cost / interactions counted;
- **projected cost at the allowance** = average × the account's limit (override and top-ups included). An
  **estimate**, shown only from 20 interactions on; flagged *incomplete* when some usage has no pricing rule
  (unpriced usage is never treated as free);
- **status**: within target (< £2.00), approaching target (≥ £2.00), over target (≥ £2.50), critical cost
  (≥ £5.00).

After every settlement (turns and memory learning) the account is evaluated in the background: each level
reached is recorded once per period (`cost_alerts`, unique per account, period and level), written to the audit
log (`cost.alert`) and published to operators as a live `cost_alert` event. Nothing else happens: no refusal, no
slower or cheaper service. Operator views: Usage → Finance → "AI cost per account" (filter by status, sort by
cost / projection / per interaction / interactions) and the account page.

## Abuse protection (separate from the allowance)

Deterministic technical safeguards against misuse and runaway expense, independent of both the interaction
allowance and the cost target. Ordinary heavy use is not abuse.
- Concurrency: `busy_concurrent` (above) and one live connection per watch (a new one replaces the old).
- Turn bounds: listening and speaking limits, the 30 s "thinking" timeout on the watch, the server idle timeout,
  the uplink stall timeout, a maximum incoming message size.
- Bounded tool and search use per request (search attempts are limited; search results are cached).
- Rate limits on logins, sign-ups, password resets, voice samples and checkout polling (`app/ratelimit.py`).
- Leases: work whose process died is expired, never re-run.

### What is still process-local (do not run several workers yet)

Billing admission is safe across processes and hosts. **The rest of the server is not ready for several
instances**:
- device connections and the hub (pairing, live events to the web app, pushing items and settings);
- the reminder loop (each process would deliver reminders);
- voice context and undo stores;
- the in-memory rate limiters (login, sign-up, voice samples);
- the provider key cache;
- mDNS advertising;
- the settlement retry queue.

Keep **one server process** (one uvicorn worker) until those move to shared stores. A second process during a
deploy, or a second host for a short overlap, is safe for billing.

### Cutover runbook (this change)

1. Back up the database (`pg_dump`).
2. Deploy the new server. On start it runs migrations `0017`–`0020`. They are additive: new tables, columns and
   indexes; existing usage, top-ups, subscriptions and notices are untouched; no history is backfilled.
3. Optional drain for a quiet switch: set `billing_settings.admission_paused = true`, so new turns get
   `service_unavailable` (older firmware: "Server busy"). Wait until no operation is `reserved`/`running`,
   deploy, then set it back to `false`.
4. Check `/readyz` (it also checks the billing database) and run one turn from a watch. The new
   `usage_operations` row should end `settled`, with its `usage_records` carrying `operation_id`, `dedup_key` and
   `period_key`.
5. Flash or OTA the new firmware when convenient. Older firmware keeps working: its turns are identified by
   session and turn number, and the new refusals show its "Server busy" screen.

### Rollback

- **Application rollback**: deploy the previous server version **without downgrading the schema**. It ignores
  the new table and columns. Stop the new version first, so no operation stays `running` and nothing new is
  admitted under two rule sets.
- **Schema downgrade** (`alembic downgrade 0019`) is refused once `usage_operations` has rows. Operations and
  their costs are never deleted to go back; if it is really needed, export them first and get a deliberate
  operator decision.
- Firmware with `request_id` works with the previous server, which ignores the field. Only the
  "Conversations in progress" screen is never shown.

## Subscription states

| Source | State | Access |
|---|---|---|
| Stripe | trialing, active, past_due | yes (past_due: Stripe is retrying the card) |
| Stripe | canceled, unpaid, incomplete… | no → `subscription_required` |
| Complimentary | active and `now < current_period_end` | yes |
| Complimentary | expired / revoked | no ("Pilot ended") |
| Internal account (`accounts.internal`) | — | always, costs still tracked |

## Watch purchase → ola Care trial at pairing

Since migration 0025, the shop sells the **watch alone** and starts ola Care when the watch is first paired. The code is in `app/billing.py` (checkout and webhooks), `app/care_terms.py` (consent text) and `app/care_activation.py` (the trial).

### 1. Before payment: consent to future charges

- `GET /api/shop/status` publishes, per currency, the ola Care terms. They are rendered from the **Stripe Care price** (amount and interval), `BUDDYAI_CARE_TRIAL_DAYS`, and the rule that the trial starts at pairing. Each version (`CARE_TERMS_VERSION`) carries its text and a SHA-256.
- The site shows that exact text next to a **required checkbox**. `POST /api/shop/checkout` refuses requests without `care_terms_accepted`, or whose version or hash differ from the current terms (409 "reload").
- Checkout Session settings:
  - `mode=payment` with the watch price only;
  - `customer_creation=always`;
  - `payment_intent_data.setup_future_usage=off_session`, so the card is saved for later charges;
  - `consent_collection.terms_of_service=required`;
  - `custom_text.terms_of_service_acceptance`, holding the same terms text;
  - metadata `kind=watch` plus the terms version and hash.
- Unchanged from before: automatic tax, shipping countries and rates, billing address and phone, promotion codes.
- **Proof of consent**: a `billing_consents` row is written when the session is created. It holds:
  - the exact text and its hash, the amount, currency, interval and trial days, and `trial_start_rule=on_pairing`;
  - the time, IP address and user agent of the site acceptance.

  The paid webhook completes it with:
  - Stripe's `consent.terms_of_service` value (must be `accepted`);
  - the customer, payment method, account and order.

  Consent rows are billing records: account deletion keeps them, as it keeps orders.

### 2. Payment → order (webhooks only, never the redirect)

| Event (session with `kind=watch`) | Effect |
|---|---|
| `checkout.session.completed`, `payment_status=paid` | Account (created or linked), order `paid`, revenue, consent completed, `care_activations` row `awaiting_pairing` (card's customer + payment method). Welcome email (7-day set-password link) and order email repeating the agreed terms. **No subscription.** |
| `checkout.session.completed`, `unpaid` (delayed methods) | Order `payment_pending`, no account, no email. |
| `checkout.session.async_payment_succeeded` | As "paid" (the pending order becomes `paid`). |
| `checkout.session.async_payment_failed` | Order `payment_failed`, "could not be paid" email. |
| `checkout.session.expired` | A `payment_pending` order becomes `cancelled`; an abandoned checkout leaves nothing. |

- The thank-you page polls `GET /api/shop/checkout-status?session_id=…`. It answers only from these rows: `processing`, `paid`, `failed`, `cancelled` or `unknown`.
- The PaymentIntent is read before anything is written. If Stripe fails, the event is released and retried, and nothing is left half done.
- If the account already pays through another Stripe customer, it keeps that customer for billing.

### 3. Pairing → trial

"Connected" means only the **server-side pairing** of a watch to the account: the customer or operator pairing endpoint, after `DeviceHub.pair`. Joining Wi-Fi, a Bluetooth session, the WebSocket or a displayed code start nothing.

1. **Claim with a lease.** One conditional `UPDATE` moves the row to `activating` (lease 120 s), from `awaiting_pairing`, `failed`, or an `activating` row whose lease has expired. If no row changes, the call is a no-op. This covers double clicks, concurrent pairing, a second watch and refreshes.
2. **Stripe first.** `Subscription.list(customer)` runs, and a subscription whose `metadata.activation_id` matches is **adopted**. Nothing is created a second time after a crash, an ambiguous error or a retry outside Stripe's 24 h idempotency window.
3. **Eligibility**, checked on every attempt. No new trial for:
   - an entitled complimentary grant (`complimentary`);
   - an internal account;
   - any earlier Stripe subscription row (`trial_used`, which covers legacy buyers and current payers);
   - missing consent (`no_consent`).

   These rows become `not_eligible`, and the account offers the normal paid Subscribe checkout.
4. **Create**: `Subscription.create` with the Care price, `trial_period_days=BUDDYAI_CARE_TRIAL_DAYS` and the saved card as `default_payment_method`.
   - Other parameters: `off_session`, `trial_settings.end_behavior.missing_payment_method=cancel`, automatic tax, and metadata `account_id`, `activation_id` and `consent_id`.
   - Idempotency key: `care-activation-<id>-<payment method>`.
   - The card also becomes the customer's invoice default, so the billing portal shows it.
5. Success: the subscription is upserted, the row becomes `active`, and the "trial started" email gives the end date, the price and how to cancel.
6. Failure: the row becomes `failed` with a code (`missing_payment_method`, `card_error`, `payment_method_invalid`, `stripe_error` or `stripe_unreachable`). The watch **stays paired**, and the account shows "Subscription setup pending" with Retry (`POST /api/me/care/activate`). Pairing never fails because of billing.
7. `customer.subscription.*` webhooks also complete an `activating` row: the metadata names it.
8. **Recovery**: `care_activation.recover_loop` runs 60 s after start, then every 5 minutes. It retries stuck rows (expired lease) and failed rows of paired watches, with exponential backoff, up to 5 automatic attempts. After that the customer's Retry, or the operator's `POST /api/accounts/{id}/care/activate`, still works, and the account view flags `needs_attention`.

The displayed plan always comes from the `subscriptions` rows that Stripe webhooks keep up to date. `care_activation` only explains the time before a subscription exists.

### 4. Who can set up a watch

`POST /api/me/devices/pair` and `/my/setup` need a confirmed email, as before, and, when Stripe is configured, at least one of:
- a paid, shipped or delivered order;
- any ola Care subscription row, including complimentary pilots;
- an already paired watch;
- an internal account.

Without Stripe, as in development or private use, nothing changes.

### 5. Legacy orders and existing customers

- **Orders from before 0025**, and any in-flight session without `metadata.kind`, keep the old path: the trial started at purchase and their subscription rows are untouched. They have no `care_activations` row, so pairing never starts anything for them.
- **Current payers or former trial users** buying another watch get an order and a `not_eligible / trial_used` activation: no second trial.
- **Complimentary pilots** keep their grant. Pairing is allowed and no trial is created.

### Local / test setup

See `deploy/README.md` §6, "Stripe TEST MODE", and the manual checklist in `docs/SETUP_TEST_CHECKLIST.md`.

## Complimentary / test access

- **Where to grant it:**
  - Operator UI: Customers → account → *Grant ola Care*, with *Extend* and *End now*.
  - CLI: `python tools/grant_pilot.py --account-id <id> --expect-device <watch name> [--days 30] [--dry-run] [--enable-enforcement]`
- **Idempotent**: an unexpired grant is returned unchanged unless it is extended. The grant refuses accounts that pay through Stripe.
- **No Stripe objects** are created and no ids are fabricated. The grant is recorded as `subscriptions.source = "complimentary"`, with the note and the operator who granted it.
- **Expiry**: after `current_period_end` the account shows "Pilot ended" and the watch receives `subscription_required`, until the grant is extended or the customer subscribes.

## Top-ups (extra interactions)

**Decision (2026-10):** a top-up adds **250 AI interactions** for £1.99 to the current period. At the internal
target (£2.50 per 1,000, about £0.0025 per interaction) 250 interactions cost about £0.63, against about £1.43
net of VAT and card fees. Review against real per-interaction costs (Finance → AI cost per account). Top-ups
bought before migration 0027 (a money allowance, `interactions` NULL) count as the plan's current top-up size for
their own period, so nothing already bought is lost.

1. `POST /api/me/topups/checkout` creates a `pending` TopUp row (`interactions` = the plan's top-up size) and a Checkout Session ("ola extra usage: 250 AI interactions"). The row's metadata holds `topup_id` and `account_id`.
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
4. **Refund policy**: `charge.refunded` marks the top-up `refunded` and withdraws its extra interactions for the period, even if some were used. A negative revenue event is recorded. The customer-facing text says so.

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

- [ ] Approve the prices commercially: Care, extra usage (250 interactions for £1.99), the 1,000-interaction allowance and the internal cost thresholds.
- [ ] Create the Stripe products and prices in **test** mode, set `BUDDYAI_STRIPE_*` test keys and the webhook secret, and run the checkout, top-up and refund journey with the Stripe CLI (`stripe listen --forward-to …/api/stripe/webhook`).
- [ ] Webhook events to enable:
  - `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `checkout.session.expired`
  - `customer.subscription.*` (created / updated / deleted / trial_will_end — `created` also completes a trial activation)
  - `invoice.paid`, `invoice.payment_failed`
  - `charge.refunded`
- [ ] Create the **watch price at £79.99** (one-off, tax-inclusive; the regular £99.99 is display-only on the site) and the Care price (£7.99/month), both in test mode first.
- [ ] Have the ola Care consent wording, the subscription terms and the "was £99.99" claim reviewed (UK price-reduction and subscription-contract rules).
- [ ] Stripe Tax and VAT registration. The extra-usage price is VAT-inclusive (`tax_behavior=inclusive`).
- [ ] Have the interaction-allowance wording reviewed (subscription terms §3a, FAQ, consent text `care-2026-12`; UK CMA / subscription rules): a clear renewal date, what counts, and the top-up refund wording.
- [x] Usage admission and reservations live in PostgreSQL (multi-process safe). Before running more than one server worker, move the other process-local parts to shared stores (see "What is still process-local").
- [ ] Live keys (`sk_live_…`) only after all of the above.
