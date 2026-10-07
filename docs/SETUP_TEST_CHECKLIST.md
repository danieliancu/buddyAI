# Manual test checklist: purchase → setup → pairing → ola Care trial

Run on a **test** deployment with **Stripe test mode** (deploy/README.md §6), never with live keys or
real customer data. Tick each line on a real Android phone, a real iPhone and a real watch with the new
firmware.

## A. Purchase (Stripe test mode)

- [ ] Site buy box: £79.99 with £99.99 struck through and "Sale"; the ola Care sentence says the trial starts when the watch is paired and nothing is charged for it today.
- [ ] "Buy now" stays disabled until the ola Care terms box is ticked; the box shows the monthly price, trial length, start at pairing and how to cancel.
- [ ] Stripe Checkout shows: the watch only (one-off), shipping, tax, the same ola Care terms by the Terms of Service checkbox, and the submit note ("ola Care is not charged today").
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
- [ ] "Which phone are you using?" shows Android and iPhone, with logos. "Change phone" works.
- [ ] An account with no paid order: "No watch order found", and pairing is refused.

## C. Android: Chrome, Bluetooth

- [ ] Watch in Wi-Fi setup: the screen shows `ola-XXXX`, the setup password and a QR code. Nearby BLE scanners (e.g. nRF Connect) see `ola-XXXX` advertising; after a successful setup and restart it is **no longer advertising**.
- [ ] Android 12+: allow "Nearby devices" when asked. Android ≤ 11: Location must be on.
- [ ] "Connect to watch" opens Chrome's chooser listing the watch. Picking it asks for the setup password.
- [ ] Wrong setup password: "does not match", and nothing else happens. After 3 wrong attempts: "Wait 30 seconds".
- [ ] The network list shows only 2.4 GHz networks, strongest first. "Refresh" works. "My network isn't listed" accepts a hidden SSID.
- [ ] Wrong Wi-Fi password: "Wrong Wi-Fi password". The watch keeps its previous settings. "Try again" goes back to the form.
- [ ] A 5 GHz-only network, or a network out of range: "couldn't reach that network" with the 2.4 GHz hint.
- [ ] Correct password: "Your watch is on …". The watch restarts, joins the Wi-Fi and shows a 6-digit code.
- [ ] Cancel the chooser: "No watch selected". Deny the Bluetooth permission: "Bluetooth permission needed". Bluetooth turned off: "Bluetooth is off". Walk away mid-setup: "Connection to the watch lost". Every failure shows the `ola-XXXX` recovery steps.
- [ ] Firefox or Samsung Internet on Android: "This browser can't…", with the recovery steps shown.
- [ ] Recovery on Android: join `ola-XXXX` with the setup password, and the portal saves the Wi-Fi.

## D. iPhone: Safari, the `ola-XXXX` network

- [ ] The iPhone choice shows only the `ola-XXXX` steps (no Bluetooth button).
- [ ] Camera → QR code on the watch → "Join". Or Settings → Wi-Fi → `ola-XXXX` → type the password. A wrong password is refused by iOS.
- [ ] The captive setup page opens by itself (or `http://192.168.4.1`). Pick the home Wi-Fi, enter its password, Save. The watch restarts and shows the code.
- [ ] Back in Safari, the setup page asks for the code.
- [ ] After setup, the old setup password no longer joins `ola-XXXX` (a new password is shown the next time).

## E. Pairing → ola Care trial

- [ ] Before pairing: the account shows "Free 90-day trial — starts when you pair your watch", and there is no subscription in Stripe.
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

- [ ] BOOT held 8 s: the reset confirmation appears, and after reset the watch shows a **new** setup password.
- [ ] Holding BOOT at power-on still enters download mode (flashing works).
- [ ] The setup screen is reachable again from Settings → Wi-Fi setup and from the "No Wi-Fi" error screen.
- [ ] Voice turns, OTA and reminders work after setup (Bluetooth is off by then).
