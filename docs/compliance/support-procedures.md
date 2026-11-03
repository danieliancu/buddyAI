# Support procedures

**DRAFT, not approved.** Prepared 3 November 2026 for the people who answer customers. Customer-facing terms are on the site (`/legal/returns/`, `/legal/warranty/`, `/legal/subscription-terms/`); if this page and those terms differ, the terms apply and this page must be corrected. Time limits here need legal review. Contact: ola@olacompanion.com.

## Before you start

- Confirm who you are talking to: reply from the account's email, or a code sent to it. Never ask for a password.
- You do not need to read a conversation for ordinary support. Your view shows timing, type, cost and errors, not the words.
- Only the principal administrator can open a conversation, one at a time. Ask them only with a real reason, such as the customer's own request, and record the ticket number as the reason.

## Returns: 14-day right to cancel

1. Check the delivery date. The customer has 14 days after the day of delivery and needs no reason.
2. Confirm in writing and send the return address.
3. The customer pays return postage unless the watch is faulty.
4. Refund within 14 days of getting the watch back, or of proof that it was sent, whichever is first. Refund in Stripe.
5. Check Care: a refund does not change the subscription by itself. If they no longer have the watch, cancel Care in Stripe and tell them.
6. Before reselling a returned watch, erase it (factory reset) and remove it from the account.

## Faulty watch

| Time since delivery | Customer's right |
|---|---|
| Up to 30 days | Reject for a full refund |
| Up to 6 months | Repair or replacement; if that fails, refund or price reduction |
| Up to 6 years (5 in Scotland) | Can claim; after 6 months they may need to show the fault was there at delivery |

1. Get the order number and a description. Look at the watch's diagnostics and firmware version.
2. Try the basics: charge it, restart it, check Wi-Fi.
3. If it is faulty, we pay the return postage.
4. Record the fault and batch, so a pattern shows up.

## Replacement

1. Create the replacement order and record the link to the original.
2. Remove the old watch from the account. Its history is erased when it moves.
3. Tell the customer that Care continues on the same account, and how to pair the new watch.
4. Check that Care was not started twice.

## Northern Ireland orders

Checkout cannot block Northern Ireland, and we are not on sale there yet. An order with a BT postcode writes `order.outside_delivery_area` to the audit log. Do not ship it. Contact the customer, then refund in full.

## Payments and Care

| Situation | Action |
|---|---|
| Payment failed | Stripe retries. The customer gets an email; point them to "Manage billing" |
| Cancel | The customer does it in "Manage billing". When access ends depends on the Stripe portal setting (`external-actions.md` #3). If they cannot, cancel in Stripe |
| Refund for Care | Refund in Stripe; check the subscription terms |
| Chargeback | Reply in Stripe with the evidence. The server does not handle disputes automatically, so record it yourself |
| Trial question | The 30 days start at first pairing, not at purchase |

## Data protection requests

Respond **within one month**. It can be extended by two months for complex requests, if you tell the person why.

1. Confirm identity.
2. Record each stage: `POST /api/accounts/{id}/requests` (see `retention-policy.md`). Nothing reminds you of the deadline, so keep your own date.
3. Handle it:

| Request | What to do |
|---|---|
| Access or copy | Show the customer "Download my data". If they cannot use it, help them; add anything held outside the app, such as support emails |
| Deletion | The customer deletes in the account. Billing records stay for the tax period. Backups clear within 14 to 30 days |
| Correction | Profile page; anything else by hand |
| Objection or restriction | Memory can be switched off; other requests go to the business owner |

4. Anything unclear, or from a third party such as a relative or the police, goes to the business owner before you reply.

## Security reports and incidents

- Vulnerability report: acknowledge within 5 working days (published on `/security/`), then pass to the technical lead.
- Possible breach: tell the incident lead **immediately**, even if unsure. The 72-hour period can already be running (`incident-response.md`).

## Security updates

1. Follow the stages on the Firmware page: development, pilot, 10 %, 25 %, 50 %, 100 %. Each move is a person's decision.
2. An automatic pause, or a failure to review, comes first. Do not resume until you know why.
3. The Watches page shows each watch's version. A watch left offline gets the update when it returns.
4. Tell customers only if they need to do something. Keep the promised support period: at least 2 years from purchase.

## Restore

If a backup is restored, follow `deploy/README.md` §8.5. The server will not start until deletions are re-applied.
