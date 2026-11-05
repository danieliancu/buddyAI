# International transfers

Status: not verified. Prepared 12 October 2026.

| Recipient | Likely destination | Mechanism proposed | Evidence in hand | Action |
|---|---|---|---|---|
| OpenAI | United States | OpenAI DPA with the UK International Data Transfer Addendum (or UK Extension to the EU-US Data Privacy Framework if OpenAI is certified — to check) | none | execute DPA; record mechanism; transfer risk assessment |
| Stripe | United States / EU | Stripe DPA (incorporates transfer terms) | none | confirm acceptance in the Stripe dashboard |
| Email provider | unknown | provider DPA | none | identify provider |
| Hosting | UK/EU (reported) | none needed if UK; UK adequacy regulations cover the EEA | none | confirm region in writing |
| Backup copies | none: business owner's own computer, no storage provider (decided 3 Nov 2026) | n/a while it stays in the UK | n/a | review if the computer leaves the UK |

The privacy notice says transfers rely on safeguards recognised under UK data protection law. That statement only becomes accurate once the actions above are complete — **this is a launch blocker** (see external-actions.md).

## Search Past Conversations (Conversation RAG) — not yet live

If enabled, conversation text and search questions go to OpenAI's embeddings API, likely in the United States: the same recipient, destination and open actions as the row above, and no data-residency setting has been verified. The website tells the user about this before they switch it on. The feature must stay off until the OpenAI transfer mechanism is in place.
