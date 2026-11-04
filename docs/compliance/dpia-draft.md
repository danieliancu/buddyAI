# Data protection impact assessment: conversations and memory

**DRAFT, not approved.** Prepared 3 November 2026 from the code and the other documents here, as a starting point for the business owner and an adviser. Conclusions, lawful bases and risk ratings are proposals. External action #16.

Controller: Ola Technologies London Ltd (company number and address: ______). Data protection contact: ______. ICO registration: ______.

## 1. Why a DPIA

The assistant listens when asked, stores what people say and can remember facts about them over time. People may speak about health, beliefs, relationships or other people. The product is worn on the body and may be used by older people or family members. An adviser should confirm whether a DPIA is mandatory; we recommend one either way.

## 2. The processing

| Step | Data | Where |
|---|---|---|
| Speech to text | Microphone audio during a turn; not stored on our servers | OpenAI |
| Reply | Transcript, recent history, notes, reminders, memories, custom instructions | OpenAI |
| Speech | Reply text | OpenAI |
| Web search | Query, approximate city from the watch's time zone | See question 1 below |
| Stored | Transcripts and replies, memories and embeddings, notes, reminders, search cache | PostgreSQL on the hosting provider |
| Memory learning | Conversation text, to extract facts; on by default, can be switched off | OpenAI |

Details and retention: `data-inventory.md`, `retention-policy.md`. People: customers and wearers; other people they mention, who have no relationship with us.

## 3. Necessity and proportionality (proposal)

| Question | Proposed answer | To confirm |
|---|---|---|
| Lawful basis for answering and history | Contract | Adviser |
| Lawful basis for memory learning | To decide: contract, legitimate interests or consent. Being on by default is the point most likely to be challenged | Adviser |
| Special-category data | Automatic memory learning does not store sensitive facts (`app/memory/policy.py`), but conversations are stored as said. An Article 9 condition needs legal review | Adviser |
| Data minimisation | Audio is not stored. Conversations have no automatic limit today (`conversation_retention_days` = 0) | Business owner: is a limit needed? |
| Transparency | Privacy notice and account controls | Legal review |
| People's rights | Self-service view, export, deletion, memory controls; requests by email within one month | Tested in code; process not rehearsed |

## 4. Risks

Ratings are proposals: likelihood × severity for the people affected.

| Risk | Likelihood | Severity | Existing measures | Remaining | Further action |
|---|---|---|---|---|---|
| Staff read private conversations | Low | High | Metadata by default, one conversation at a time for the principal role only, reason and audit (`content-access-audit.md`) | Medium: whoever runs the server can reach the database | Limit who has server access; review the audit log regularly |
| Database or backup stolen | Low | High | Password hashing; backups encrypted with `age`, private key kept apart on removable media | High until disk encryption and the backup encryption are confirmed | External actions #5, #26 |
| Sensitive details sent to a processor abroad | Certain (by design) | Medium | `store=false`, disclosure | High until the DPA and transfer terms are signed | External action #2; check retention and ZDR |
| Words of a third party stored | Likely | Medium | Owner can delete | Medium | Explain in the notice and guidance |
| Someone else uses the watch or sees its screen | Possible | Medium | Per-watch settings | Medium | Consider a screen lock; explain in the manual |
| Inaccurate memory affects answers | Possible | Low to medium | User can view, edit and forget | Low | None |
| Children use the service | Possible | Medium | The notice says adults only; no age check | Medium | Review imagery and guidance |
| Data returns after a restore | Low | Medium | Outbox, ledger, block on start (`backup-and-recovery.md`) | Medium: no off-site ledger, by decision; deletions after the last backup return if the whole server is lost | Handle by hand as an incident; adviser to review this accepted risk |
| Data kept too long | Likely | Medium | Self-service deletion, retention job | Medium | Approve periods, external action #17 |
| Malicious or defective firmware | Low | High | Signed release images, staged rollout, rollback | Medium until tested on watches and Secure Boot is on | External action #7 |

## 5. Questions to answer before approval

1. Production runs `ddgs_brave` (owner read `BUDDYAI_SEARCH_BACKEND=ddgs_brave` in Coolify on 4 November 2026). Its providers are now in `processor-register.md` and the inventory: Brave, the DDGS engines, page fetching, MET Norway and Nominatim, with OpenAI hosted search still used for transport by default. Still open for each: terms of use, role, processing location and transfer mechanism.
2. Is the optional conversation search on its feature branch going ahead? It adds derived data and is not covered here (`docs/CONVERSATION_RAG.md` on that branch).
3. Should memory learning be on by default?
4. Should conversations have an automatic retention limit?
5. Does a voice product used near others raise any further duty? The watch records only after a tap.
6. Should the ICO be consulted? That applies if a high risk remains after the measures.

## 6. Sign-off

| Item | Name | Date | Decision |
|---|---|---|---|
| Measures approved | | | |
| Remaining risks approved | | | |
| Adviser's opinion | | | |
| Review date | | | |
