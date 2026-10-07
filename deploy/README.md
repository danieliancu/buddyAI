# Going live: server runbook

This takes ola from your PC to a rented cloud server with HTTPS, PostgreSQL and daily backups.
Allow about 2 hours the first time. You need a credit card, a domain name, and this repository.

```
Internet ──► Caddy (HTTPS, ports 80/443)
               ├─ www.example.com  → marketing site (static) + /api/shop/* → server
               ├─ app.example.com  → customer app + operator app (/admin) + REST API + Stripe webhook
               └─ api.example.com  → watches: /ws/device (WebSocket) and /fw/* (firmware downloads)
            server (FastAPI) ──► PostgreSQL        backup (nightly pg_dump → deploy/backups)
```

## 1. Rent the server

**Recommended: Hetzner Cloud** (EU, cheapest, good latency for the UK and the EU).

1. Create an account at <https://console.hetzner.cloud>, then a project called "ola".
2. Add your SSH key under **Security → SSH keys**. If you don't have one, run
   `ssh-keygen -t ed25519` in PowerShell and paste the content of `~\.ssh\id_ed25519.pub`.
3. **Add server**:
   - Location: Nuremberg or Falkenstein (Germany) or Helsinki.
   - Image: **Ubuntu 24.04**.
   - Type: **CPX21** (3 vCPU, 4 GB RAM), about €8/month. This is enough for the first few hundred
     watches, because the AI runs at OpenAI. You can resize later.
   - Networking: IPv4 + IPv6.
   - SSH key: the one you added.
   - Backups: optional. Hetzner server snapshots add 20% to the price; the database backups below are separate.
4. **Firewalls → Create firewall**:
   - allow inbound TCP 22 (only from your own IP if possible), TCP 80, TCP 443 and UDP 443;
   - apply it to the server.
5. Note the server's public IPv4 address.

Alternative: DigitalOcean (London region, about £15/month). The steps are the same.

## 2. Domain and DNS

1. Buy a domain, e.g. at Cloudflare Registrar or Namecheap (about £10/year).
2. Create these DNS records, all pointing to the server IPv4. Add AAAA records for IPv6 if you like.

| Type | Name | Value |
|---|---|---|
| A | `@` (example.com) | server IP |
| A | `www` | server IP |
| A | `app` | server IP |
| A | `api` | server IP |

With Cloudflare, set these records to **DNS only** (grey cloud) so Caddy can get its certificates.
WebSockets from the watches also go straight to your server.

## 3. Install Docker and the code

```bash
ssh root@<server-ip>
apt update && apt -y upgrade
curl -fsSL https://get.docker.com | sh
adduser --disabled-password --gecos "" buddyai && usermod -aG docker buddyai
su - buddyai
git clone <your private repository URL> buddyai && cd buddyai
cp deploy/.env.example deploy/.env && nano deploy/.env      # fill in, see sections 4–6
```

If the code isn't in a git hosting service yet, push it to a **private** GitHub repository first,
or copy it with `scp -r`.

## 4. Configure `deploy/.env`

- **Domains and `ACME_EMAIL`.**
- **`POSTGRES_PASSWORD`:** generate with `openssl rand -base64 32`.
- **`BUDDYAI_OPENAI_API_KEY`.** In the OpenAI dashboard:
  - set a **monthly spend limit** as a safety net;
  - sign OpenAI's **Data Processing Addendum** (needed for GDPR);
  - check the data-residency options for EU customers.
- **Email:** any SMTP provider works (Resend, Postmark, Amazon SES, Zoho…). Verify your domain with
  the provider (SPF/DKIM DNS records) so verification and order emails don't land in spam.
- **Stripe:** see section 6. You can leave it empty to launch with the shop closed.

## 5. Start

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs -f server     # Ctrl+C to leave
docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec server python -m app.cli create-operator <your-name>
```

1. Open `https://app.<domain>/admin/login` and sign in as operator. Web setup is disabled in
   production, so nobody else can claim the operator account.
2. Go to **System → Test connection** and check LLM, STT and TTS.
3. Open `https://www.<domain>` and check the marketing site.
4. Uptime monitoring: add `https://app.<domain>/readyz` to a free monitor (UptimeRobot, Better Stack).
   It alerts you if the server or the database stops answering.

## 6. Stripe (shop + ola Care subscription)

Everything below is done in **test mode** first. Switch to live mode only after a full test purchase.
How it works: `docs/BILLING.md` ("Watch purchase → ola Care trial at pairing"). The watch is charged once
at checkout and the card is saved; the ola Care subscription (with its free trial) is created only when
the watch is paired to the customer's account.

1. **Account:** create it, then complete your business details and bank account.
2. **Products** (Product catalogue):
   - **"ola Watch"**: a *one-off* price in GBP (currently the sale price, **£79.99**) and one in EUR. The
     regular £99.99 shown struck through on the site is display only (`site/src/config.ts`).
   - **"ola Care"**: a *recurring monthly* price in GBP (£7.90) and one in EUR. The server reads its
     amount to write the consent text, and adds the trial (`BUDDYAI_CARE_TRIAL_DAYS`) when the watch is paired.
   - Copy the four `price_…` ids into `deploy/.env` (`BUDDYAI_STRIPE_PRICE_WATCH_GBP/EUR`,
     `BUDDYAI_STRIPE_PRICE_CARE_GBP/EUR`).
3. **Tax:** enable Stripe Tax and add your registrations: UK VAT, and **EU OSS** for EU consumers.
   Mark prices as tax-inclusive or exclusive, consistently with the website.
4. **Shipping:** create shipping rates (e.g. "UK standard", "EU standard") in GBP and EUR and put
   their `shr_…` ids in `.env`.
5. **Checkout settings:**
   - set your Terms of Service and Privacy Policy URLs (`https://www.<domain>/legal/...`);
     Checkout shows the ola Care terms next to the terms checkbox, so the ToS URL is required;
   - payment methods: keep only methods that can be saved for later (cards, wallets). Checkout with
     `setup_future_usage` hides the others automatically.
6. **Customer portal:** enable it, allowing customers to update cards, see invoices and cancel.
7. **Webhook:**
   - add the endpoint `https://app.<domain>/api/stripe/webhook`;
   - select the events:
     - `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `checkout.session.expired`;
     - `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `customer.subscription.trial_will_end`;
     - `invoice.paid`, `invoice.payment_failed`;
     - `charge.refunded`;
   - copy the signing secret into `BUDDYAI_STRIPE_WEBHOOK_SECRET`.
8. Restart the server:
   `docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d server`.
9. **Test purchase** (test card `4242 4242 4242 4242`, any future date, any CVC). On the website,
   tick the ola Care terms box and click **Buy now**. Then check that:
   - the thank-you page says "Confirming your payment…", then "Payment confirmed";
   - an order appears under **Admin → Orders**, and the account's ola Care shows "starts when you
     pair your watch" (no subscription in Stripe yet);
   - after pairing a watch, Stripe shows a **trialing** subscription on the same customer, with the
     saved card as default payment method.

   The full manual list is in `docs/SETUP_TEST_CHECKLIST.md`.
10. Repeat with live keys when everything works.

### Stripe TEST MODE: local machine and a Coolify test environment

Never commit keys or secrets: they go in `server/.env` (local, git-ignored) or in the Coolify
environment variables of a **test** resource, never the production one.

- **Local**
  1. In the Stripe dashboard, switch to *Test mode*.
  2. Copy the test secret key (`sk_test_…`) and the four test `price_…` ids into `server/.env`:
     ```
     BUDDYAI_STRIPE_SECRET_KEY=sk_test_...
     BUDDYAI_STRIPE_PRICE_WATCH_GBP=price_...
     BUDDYAI_STRIPE_PRICE_CARE_GBP=price_...
     BUDDYAI_SITE_URL=http://localhost:4321
     BUDDYAI_APP_URL=http://localhost:5173
     ```
  3. Forward webhooks with the Stripe CLI:
     `stripe listen --forward-to http://127.0.0.1:8765/api/stripe/webhook`. It prints a `whsec_…`
     signing secret: put it in `BUDDYAI_STRIPE_WEBHOOK_SECRET` and restart the server.
  4. Run the site (`npm run dev` in `site/`) and the app (`npm run dev` in `web/`).

  Web Bluetooth needs a secure page. `http://localhost` counts as one on the PC, but a phone needs
  HTTPS: test Bluetooth setup on a test deployment, or through an HTTPS tunnel to the app.
- **Coolify test environment**: a separate application with its own domain and database. Set the
  same `BUDDYAI_STRIPE_*` variables with **test** values, and add a test-mode webhook endpoint in
  Stripe pointing at that domain (its own `whsec_…`).
- **Test cards**:
  - `4242 4242 4242 4242`: success.
  - `4000 0025 0000 3155`: 3-D Secure.
  - `4000 0000 0000 9995`: declined.
  - To test a failed ola Care activation and the Retry: after a test purchase, detach the saved card from the customer in the Stripe dashboard, then pair the watch. The account shows "Subscription setup pending". Add a card in "Manage billing", then press Try again.
- **Delayed payments**: methods such as Bacs Direct Debit or SEPA exercise `payment_pending`, then
  `async_payment_succeeded` or `async_payment_failed`.

**Shipping to the EU from the UK (or the reverse)** involves customs. For consumer parcels up to
€150, register for the EU **IOSS** scheme, or ship from an EU fulfilment partner. Talk to your
accountant before the first EU sale.

## 7. Updates

```bash
cd ~/buddyai && git pull
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

Database migrations run automatically when the server starts.

Firmware updates: build a signed release image (see `firmware/README.md`), upload it under
**Admin → Firmware**, then send the OTA update to the watches.

## 8. Backups and restore

- The `backup` service writes a database dump every 24 hours to `deploy/backups/` and keeps 14 days.
- **Copy the dumps off the server.** A server failure would otherwise take the backups with it. Options:
  - a Hetzner Storage Box (from about €4/month) with `rclone`;
  - any S3 bucket with `restic`.
- **Test a restore** at least once, on a test server:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec -T postgres \
  pg_restore -U buddyai -d buddyai --clean --if-exists < deploy/backups/buddyai-YYYYMMDD-HHMM.dump
```

- Also back up the `serverdata` volume. It holds the cookie secret, firmware images and any keys
  entered in the web app:
  `docker run --rm -v buddyai_serverdata:/d -v $PWD:/b alpine tar czf /b/serverdata.tgz -C /d .`

## 9. Memory (long-term memory, staged rollout)

Memory lets the watch keep facts the user asks it to remember ("remember that my granddaughter is called
Maria"). Remembering on request and learning from conversations are **on by default**; vectors stay off until
you switch them on. Each customer can switch off saving, using memories and learning, each on its own, on
their Memory page. Before this goes live, the privacy policy must describe memory and learning. Design and rules: `docs/MEMORY.md`. Every switch below can be undone
by setting it back (nothing is deleted). Never roll the server image back to a
release older than the newest migration that has run: the server would not start. Use the switches instead.

On Coolify, set the variables in the resource's Environment Variables and redeploy. With the plain compose
file, add them to `deploy/.env` and run the update command from section 7.

### Milestone A: remember on request (no database change beyond migration 0021)

1. Take a fresh dump and test that it restores (section 8). Keep a copy off the server.
2. Deploy the release. Migration 0021 adds the `memories` and `memory_jobs` tables. Check `/readyz` and a few
   normal watch conversations.
3. Memory is on for everyone after this deploy. To try it on your own accounts first, set
   `BUDDYAI_MEMORY_ACCOUNTS=<your account ids, comma-separated>` **before** deploying (or
   `BUDDYAI_MEMORY_ENABLED=false` to keep it off entirely). Test on a watch: "remember that…", start a new
   conversation (30 minutes later, or another day), ask about it, then "forget that". Check the **Memory** page
   in the web app (the three switches) and the data export.
4. Everyone: empty `BUDDYAI_MEMORY_ACCOUNTS`.

### Milestone B: pgvector, semantic recall, learning

pgvector is a PostgreSQL extension. The database image changes from `postgres:16-alpine` to
`deploy/postgres.Dockerfile`: the same image with the pgvector files added (same PostgreSQL 16, same Alpine C
library), so the data volume is used as it is.

1. Take a fresh dump, copy it off the server, test the restore **with the new image** on a test machine:

   ```bash
   docker build -f deploy/postgres.Dockerfile -t buddyai-postgres:pgvector .
   docker run --rm -d --name restore-test -e POSTGRES_USER=buddyai -e POSTGRES_PASSWORD=test -e POSTGRES_DB=buddyai buddyai-postgres:pgvector
   docker exec -i restore-test pg_restore -U buddyai -d buddyai --no-owner < buddyai-YYYYMMDD-HHMM.dump
   docker exec restore-test psql -U buddyai -d buddyai -c "select count(*) from turns"
   docker rm -f restore-test
   ```

2. In a quiet hour, deploy the release that contains the database image change. The database restarts once
   (a few seconds; the server reconnects by itself). Check that the extension files are there:

   ```bash
   docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec postgres      psql -U buddyai -d buddyai -c "select name, default_version from pg_available_extensions where name='vector'"
   ```

   Migration 0022 then creates the extension and the `memory_embeddings` table when the server starts. If the
   extension were missing, 0022 stops with a clear message and changes nothing; the server does not start,
   so redeploy the previous release and fix the image. Once 0022 has run, keep the pgvector image.
3. `BUDDYAI_MEMORY_EMBEDDINGS_ENABLED=true`: existing memories get their vectors in the background. The cost
   shows in **Usage** as kind `embedding` (tiny).
4. `BUDDYAI_MEMORY_VECTOR_RETRIEVAL=true`: accounts with more than 30 memories get semantic recall. Watch the
   time to first audio in **Usage → Diagnostics** before and after; the query embedding is capped at 350 ms.
5. Learning from conversations is on by default (`BUDDYAI_MEMORY_INFERENCE_ENABLED=true`; set `false` to stop
   it for everyone). Customers can switch it off on their Memory page. Costs show as operations of kind
   `memory` (one small model call per conversation, counted in the customer's allowance).

What to watch: the server log lines `memory retrieve|save|forget|extract|job` (account id, path, timing, never
memory text) and `GET /api/diagnostics` → `memory` (job queue and failures).

## 10. Before selling: non-code checklist

- [ ] Company registered.
- [ ] UK VAT registration; EU OSS (+ IOSS if shipping from the UK).
- [ ] ICO registration (UK data protection fee).
- [ ] Privacy policy, terms of sale and subscription terms **reviewed by a lawyer**. The site ships
      them as DRAFTs.
- [ ] Product compliance for the watch: UKCA/CE marking, RED (radio) test report, WEEE and battery
      registration.
- [ ] A consumer-ready enclosure and strap.
- [ ] Measured battery life, published on the site.
- [ ] Firmware release image built with the production signing key (keep the key offline, with a backup).
- [ ] Secure Boot + flash encryption enabled on **production units only** (see `firmware/README.md`).
- [ ] Test purchase end-to-end in Stripe test mode, then one real purchase with live keys and a refund.
- [ ] Beta with 2–3 friendly customers before the public launch.
