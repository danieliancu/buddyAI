# External actions (cannot be done in software)

None of these is complete. Mark an item done only with evidence (document, receipt, report) stored outside the public repository.

| # | Action | Owner | Priority | Notes |
|---|---|---|---|---|
| 1 | ICO data protection fee: assess tier and pay; record the registration number | Business owner | BLOCKER | add the number to the privacy notice once issued |
| 2 | Execute the OpenAI DPA (incl. UK transfer terms); review data controls, abuse-monitoring retention, ZDR eligibility, data residency | Business owner + legal | BLOCKER | privacy notice relies on transfer safeguards |
| 3 | Confirm Stripe DPA acceptance and transfer terms; check portal cancellation settings and Smart Retries schedule | Business owner | HIGH | |
| 4 | Identify the production email provider; accept its DPA; set `BUDDYAI_EMAIL_BACKEND=smtp` | Business owner | BLOCKER | console backend means no emails are delivered |
| 5 | Confirm hosting provider, region (UK/EU) and DPA; check disk encryption options and provider snapshots | Business owner | HIGH | encryption at rest not claimed until done |
| 6 | Provision off-site backup storage (UK/EU), generate the age key pair **offline**, set `BACKUP_AGE_RECIPIENT` / `BACKUP_RCLONE_REMOTE`; run a restore test | Business owner / operator | HIGH | see backup-and-recovery.md |
| 7 | Real-device security tests on a spare unit: signed OTA, foreign-key rejection, rollback, Secure Boot, flash/NVS encryption, JTAG/download mode, random setup password | Engineering | BLOCKER (before production units) | firmware/README.md checklist |
| 8 | Product conformity: UKCA route (RED radio test report incl. EN 18031 cyber requirements, EMC, safety), technical file, Declaration of Conformity, labelling; decide whether Waveshare's own certifications help | Business owner + test lab | BLOCKER | importer/brand owner may carry manufacturer duties |
| 9 | Battery: obtain the EEMB LP402535 datasheet, IEC 62133 and UN 38.3 reports, MSDS; battery producer registration | Business owner + supplier | BLOCKER | shipping lithium batteries needs UN 38.3 |
| 10 | WEEE producer registration and take-back arrangement | Business owner | BLOCKER | |
| 11 | RoHS / REACH declarations from the board supplier | Business owner + supplier | HIGH | |
| 12 | PSTI statement of compliance: approve the draft (psti-assessment.md), complete manufacturer details, sign, supply with the product | Business owner + legal | BLOCKER | support period: at least 2 years from purchase (decided 12 Oct 2026) |
| 13 | Company identity: full registered office address and company number on the site and in emails | Business owner | BLOCKER | not invented in code |
| 14 | VAT: monitor the registration threshold; if registered, add the VAT number and restore VAT wording | Business owner + accountant | MEDIUM | |
| 15 | Legal review of privacy notice, terms of sale, subscription terms, returns, warranty, security page | Lawyer | BLOCKER | |
| 16 | DPIA for conversation and memory processing | Business owner + adviser | HIGH | possible special-category data |
| 17 | Confirm retention periods proposed in retention-policy.md (billing records, consents, audit log, logs) | Business owner + adviser | HIGH | |
| 18 | Independent security assessment / penetration test before or soon after launch | Business owner | HIGH | |
| 19 | Publish product information (size, weight, water resistance) | Business owner | HIGH | required before sale |
| 20 | Enable operator MFA for every operator, then set `BUDDYAI_ADMIN_MFA_REQUIRED=true` | Operators | HIGH | after deploying this branch |
