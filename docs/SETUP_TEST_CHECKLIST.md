# Manual test checklist: purchase → setup → pairing → ola Care trial

Run on a **test** deployment with **Stripe test mode** (deploy/README.md §6), never with live keys or
real customer data. Tick each line on a real watch with the new firmware; any phone or computer works for
the account (there is no phone-specific step).

## A. Purchase (Stripe test mode)

- [ ] Site buy box: £119, with no struck-through "was" price and no "Sale" label; the ola Care sentence says the trial starts when the watch is paired and nothing is charged for it today.
- [ ] "Buy now" stays disabled until the ola Care terms box is ticked; the box shows the monthly price, trial length, start at pairing and how to cancel.
- [ ] Stripe Checkout shows: the watch only (one-off), shipping, tax, the same ola Care terms by the Terms of Service checkbox, and the submit note ("ola Care is not charged today").
- [ ] Discount code: in Admin → Discount codes create **VIP** at £79.99. At Checkout, "Add promotion code" → `VIP`: the watch shows £119 − £39.01 and the total is £79.99 + shipping. Admin → Orders shows "VIP −£39.01" on the order. Switch VIP off: Checkout refuses the code. Pairing still starts a 30-day olá Care trial at £7.99.
- [ ] Pay with `4242 4242 4242 4242`. The thank-you page shows "Confirming your payment…", then "Payment confirmed". Open the thank-you URL with a made-up `session_id`: it must **not** claim success.
- [ ] In the Stripe test dashboard:
  - the PaymentIntent has `setup_future_usage=off_session`;
  - the customer has the card saved;
  - there is **no subscription**.
- [ ] In the operator app:
  - the order is `paid`;
  - the account was created;
  - `billing_consents` has a row with `status=accepted` and the exact terms text;
  - `care_activations` shows `awaiting_pairing`.
- [ ] Emails:
  - "Welcome to ola — set your password", with a link valid 7 days that opens "Set your password";
  - order confirmation repeating the ola Care terms.
- [ ] 3-D Secure card `4000 0025 0000 3155`: the challenge passes and the order is paid. Declined card `4000 0000 0000 9995`: no order.
- [ ] Delayed method (for example a Bacs or SEPA test account):
  - after checkout the order is `payment_pending` and the thank-you page says the payment is still being confirmed;
  - after the async success webhook the order becomes `paid` and the emails arrive;
  - with the async failure test value: the order becomes `payment_failed`, the "could not be paid" email arrives, and no account can set up a watch.
- [ ] Abandon a checkout (close the tab) and let it expire: no order, no account.
- [ ] Re-send a webhook from the Stripe dashboard (same event): nothing is duplicated.

## B. Account → setup page

- [ ] The welcome link sets the password and lands on `/my/setup`. Sign out and sign in again: setup continues where it was.
- [ ] Logo in the account goes to the website. On the website (signed in): the header shows the first name with a green lock instead of "Sign in"; signed out it shows "Sign in".
- [ ] The setup page shows the three steps (Wi-Fi on the watch → type the code → done) and the code form straight away: no phone choice, no Bluetooth or QR instructions. "No code on the watch?" opens the help (2.4 GHz, Settings → Wi-Fi setup, 8 s reset).
- [ ] An account with no paid order: "No watch order found", and pairing is refused.

## C. Wi-Fi on the watch

- [ ] New watch (or after a factory reset): on first start it opens the Wi-Fi screen by itself.
- [ ] The list shows nearby 2.4 GHz networks, strongest first, with a lock on protected ones; refresh works. 5 GHz-only networks are not listed.
- [ ] Tap a protected network: the keyboard opens. Fewer than 8 characters: "At least 8 characters", nothing is sent.
- [ ] Wrong Wi-Fi password: "Wrong password" on the watch; the network is not saved and the list is offered again.
- [ ] A network that went out of range (or was switched off) after it was picked: a clear "network not found" message, back to the list.
- [ ] Correct password: "Connecting…", then the watch goes online and shows a 6-digit code. Leave it 5 minutes: a new code appears by itself.
- [ ] Settings → Wi-Fi setup: add another network the same way; the saved networks are listed and can be forgotten. A mistyped password never replaces a network that works.
- [ ] Factory reset (BOOT held 8 s, confirm): the watch forgets its networks and opens the Wi-Fi screen again.
- [ ] No `ola-XXXX` network and no Bluetooth advertising at any time (check with a phone's Wi-Fi list and a BLE scanner such as nRF Connect).

## E. Pairing → ola Care trial

- [ ] Before pairing: the account shows "Free 30-day trial — starts when you pair your watch", and there is no subscription in Stripe.
- [ ] Enter the 6-digit code. The watch is paired, and:
  - Stripe shows **one** `trialing` subscription on the card's customer, with the saved card as default payment method and metadata `activation_id`;
  - the account shows "Free trial until <date>, then £7.99 a month…";
  - the "free trial has started" email arrives.
- [ ] Pair a second watch, and press Retry or refresh: still exactly one subscription.
- [ ] Failure path: detach the saved card in Stripe before pairing, then pair. The watch is paired, and the account says "Subscription setup pending" (never "active"). Add a card in "Manage billing", then "Try again": one subscription.
- [ ] Crash path, optional: stop the server right after pairing while the activation is `activating`, then start it again. Within about 5 minutes the activation becomes `active` with one subscription (or the existing one is adopted).
- [ ] Complimentary pilot account buying a watch: pairing works, no trial is created, and the plan stays complimentary.
- [ ] Legacy customer (an order from before this change, with a trial that started at purchase): pairing works, nothing new in Stripe. Buying another watch: no second trial ("already used its free trial").
- [ ] Stripe test clock, optional: advance past the trial. The first invoice is paid with the saved card and the account shows "Active — renews …".

## F. Regressions

- [ ] BOOT held 8 s: the reset confirmation appears, and after reset the watch opens the Wi-Fi screen.
- [ ] Holding BOOT at power-on still enters download mode (flashing works).
- [ ] The Wi-Fi screen is reachable again from Settings → Wi-Fi setup and from the "No Wi-Fi" error screen.
- [ ] Voice turns, OTA and reminders work after setup.
