# Consumer rights and subscription experience (Great Britain)

Prepared 12 October 2026 from the site, the web app and `server/app/billing.py`. Engineering findings, not legal advice. Items marked **Legal review** need a qualified adviser.

## What the product does today

| Area | Implementation | Evidence | Status |
|---|---|---|---|
| Watch price | £79.99 one-off (shown with a struck-through "was" price) | `site/src/config.ts` | IMPLEMENTED_NOT_VERIFIED — a "was" price needs a genuine prior selling price (**Legal review**, pricing practices guidance) |
| VAT | business **not VAT-registered**; "including VAT" wording removed on this branch | site pages | IMPLEMENTED (branch) |
| Delivery | costs shown at Stripe checkout; delivery within 30 days | terms-of-sale §2, §4 | IMPLEMENTED_NOT_VERIFIED |
| Contract formation | on confirmation email | terms-of-sale §3 | IMPLEMENTED_NOT_VERIFIED |
| 14-day cancellation (goods) | from receipt; cancel by email; model form; refund within 14 days; customer pays return postage unless faulty | returns page | IMPLEMENTED_NOT_VERIFIED (**Legal review**) |
| Faulty goods | statutory rights only (CRA 2015: 30-day reject, repair/replace, 6 years to claim); no voluntary warranty | warranty page | IMPLEMENTED_NOT_VERIFIED |
| olácare price | £7.99/month (the brief mentions £8 — the site and Stripe price use £7.99; not changed) | config.ts, Stripe price ids | — |
| Trial | 30 days, starts at **first pairing**, card saved at checkout with tick-box consent, reminder email before the trial ends (`trial_will_end` webhook) | `care_activation.py`, `billing.py:732-739`, SetupPage, PlanCard | IMPLEMENTED; site wording aligned to "30-day" on this branch |
| Renewal | monthly, automatic until cancelled | subscription terms | IMPLEMENTED |
| Cancellation | Account → Manage billing (Stripe portal); ends at period end; access until then; "Nothing more will be charged" shown | PlanCard.tsx, billing.portal_url | IMPLEMENTED; portal cancellation behaviour depends on Stripe portal settings — **verify in the Stripe dashboard** |
| Refunds on cancellation | none for the current period | subscription terms (branch) | **Legal review** with the 14-day right for services |
| Failed payments | Stripe retries (Smart Retries per dashboard); `past_due` keeps access | `billing.py:41` | IMPLEMENTED_NOT_VERIFIED — retry schedule lives in the Stripe dashboard |
| Account deletion | cancels the subscription immediately in Stripe (branch); refuses to delete if Stripe cancellation fails | `accounts.py`, `billing.py` | IMPLEMENTED (branch), tests |
| Company identity | name "Ola Technologies London Ltd"; address "Essex, United Kingdom"; company number empty; VAT empty | config.ts | **NOT_IMPLEMENTED** — full registered office address and company number must be added (external action) |

## Open legal points

1. **Digital content / service cancellation:** olácare is a service starting at pairing; how the 14-day right applies when the trial starts after delivery needs advice.
2. **DMCC Act 2024 subscription contract rules** (reminder notices, easy exit, cooling-off for renewals): announced; **check the commencement date** before relying on or implementing them. Today the product sends a trial-end reminder and allows online cancellation.
3. **"Was" price** on the watch.
4. **Pre-contract information**: size, weight and water resistance are still not published (the site says "before shipping").
5. **Imagery showing children** vs the 18+ age limit in the privacy notice.
