# Incident response plan

**DRAFT, not approved.** Prepared 3 November 2026 for review by the business owner and an adviser. Names, phone numbers and the ICO registration number are blank until they exist. This is not legal advice.

Use this plan for a security incident or a personal data breach: anything that may have exposed, changed, lost or destroyed customer data, or that compromises the service, the watches or firmware signing.

## Roles

| Role | Does what | Who |
|---|---|---|
| Incident lead | Decides, keeps the log, closes the incident | ______ |
| Technical lead | Investigates, contains, restores | ______ |
| Communications | ICO, customers, processors | ______ |
| Adviser | Legal view on notification | ______ |

In a small team one person can hold several roles. Keep a named deputy for the incident lead.

## Severity

| Level | Examples | Start within |
|---|---|---|
| Critical | Customer conversations or accounts exposed; database or backup stolen; firmware signing key lost; malicious firmware published | Immediately |
| High | Operator account taken over; a defective firmware rollout on many watches; a provider key leaked | 1 hour |
| Medium | Limited exposure, contained; repeated attacks with no known success | Same working day |
| Low | Vulnerability report with no sign of use | Acknowledge within 5 working days (published on `/security/`) |

## Steps

1. **Record.** Open a private incident log with the time you became aware. The 72-hour period below usually starts then. Note every decision and its time.
2. **Assess.** What happened, which data, how many people, is it still going on?
3. **Contain.** Use the actions below. Keep evidence: copy logs and the audit log before anything rotates or expires.
4. **Decide on notification** (next section), and record the decision even if you do not notify.
5. **Fix and recover.** Remove the cause. Restore if needed (`deploy/README.md` §8), including re-applying deletions (§8.5). Backup copies are on the business owner's computer, and there is no off-site copy of recent deletions: if the server is lost, what customers deleted after the last copy comes back and must be handled by hand.
6. **Review** within two weeks: cause, what worked, what changes. Update this plan.

## Containment actions

| Situation | Action |
|---|---|
| Operator account compromised | `python -m app.cli disable-operator <name>`; review `content.reveal`, `content.access_refused`, `firmware.*`, `system.keys_change` in the audit log |
| Provider or Stripe key leaked | Rotate at the provider, update the server, review provider usage |
| Server secret leaked | Rotate the secret key (signs everyone out), rotate database password |
| Defective firmware | Pause the release on the Firmware page. A pause stops new offers; watches that already installed it stay on it, so publish a fixed, higher version |
| Firmware signing key lost or leaked | Stop all rollouts. Follow `firmware/keys/README.md`. Treat as Critical: watches trust that key |
| Database or backup exposed | Rotate credentials. Check the encryption status of what was exposed (see `backup-and-recovery.md`): the live database has no application-level encryption |
| Computer holding backup copies, or a key stick, lost or stolen | Treat as Critical until you know the copies were `.age` files and the key was not with them. If a key stick is lost, generate a new key, set it on the server, and keep the other stick until the old backups expire. Assess under the notification section below |
| Watch token misuse | Revoke the watch from the operator app |

## Notifying the ICO

Under UK GDPR, a personal data breach must be reported to the ICO **within 72 hours of becoming aware of it**, unless it is unlikely to result in a risk to people's rights and freedoms. If you do not have all the details, report what you know and add the rest later. If you report late, explain why.

The report needs: what happened; the categories and approximate numbers of people and records; the contact point; the likely consequences; what has been done.

Report through the ICO's website or helpline (`ico.org.uk`, 0303 123 1113). ICO registration number: ______ (not issued yet).

## Telling customers

If the breach is likely to result in a **high risk** to people, tell them without undue delay, in clear language: what happened, the likely consequences, what we have done, what they should do, and how to contact us. Conversations can contain health or other sensitive details, so treat their exposure as likely high risk unless an adviser concludes otherwise.

## Telling or hearing from processors

Check each contract for its notification terms (`processor-register.md`). None of the data processing agreements is evidenced as signed yet (`external-actions.md`).

## Records

Keep every breach on file, reported or not: facts, effects and action taken. Store this outside the public repository.

## Gaps today

- Alerts cover costs, the rollout and the deletion ledger. There is no external uptime monitor, pager or alert on failed sign-ins.
- Production log retention and contents have not been verified.
- This plan has not been rehearsed. Run one table-top exercise before launch.
