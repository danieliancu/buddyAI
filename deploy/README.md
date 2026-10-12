# Going live: server runbook

This takes ola from your PC to a rented cloud server with HTTPS, PostgreSQL and daily backups.
Allow about 2 hours the first time. You need a credit card, a domain name, and this repository.

```
Internet ──► Caddy (HTTPS, ports 80/443)
               ├─ www.example.com  → marketing site (static) + /api/shop/* → server
               ├─ app.example.com  → customer app + operator app (/admin) + REST API + Stripe webhook
               └─ api.example.com  → watches: /ws/device (WebSocket) and /fw/* (firmware downloads)
            server (FastAPI) ──► PostgreSQL        backup (daily pg_dump → deploy/backups, encrypted off-site)
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
   - **"ola Care"**: a *recurring monthly* price in GBP (£7.99) and one in EUR. The server reads its
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
     Without Stripe Tax or a Terms of Service URL in the test dashboard, add
     `BUDDYAI_STRIPE_AUTOMATIC_TAX=false` and `BUDDYAI_STRIPE_REQUIRE_TOS=false`. They are local test
     switches: they are ignored with a live key. Consent then rests on the site's ola Care checkbox, and the
     terms are shown above Stripe's Pay button.
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

The `backup` service (image built from `deploy/backup/`, scripts `backup.sh`, `run.sh`, `restore.sh`)
runs once at start and then every `BACKUP_INTERVAL_S` seconds (default 24 hours). Each run:

1. dumps the database (`pg_dump -Fc`) to `deploy/backups/buddyai-YYYYMMDD-HHMM.dump`
   (Coolify: the `backupdata` volume);
2. checks the dump with `pg_restore --list` (an unreadable dump is a failed run);
3. writes `buddyai-YYYYMMDD-HHMM.dump.sha256` (checksum of the plaintext dump, `sha256sum -c` format);
4. with `BACKUP_AGE_RECIPIENT`: encrypts it to `….dump.age` and deletes the plaintext dump;
5. with `BACKUP_RCLONE_REMOTE`: uploads the `.age` file and the `.sha256` off-site, then deletes
   `buddyai-*` files older than `BACKUP_REMOTE_KEEP_DAYS` (default 30) on the remote.
   **Unencrypted backups are never uploaded**: a remote without a recipient is a failed run;
6. with `BACKUP_SERVERDATA=1`: the same for `buddyai-serverdata-YYYYMMDD-HHMM.tar.gz` (see 8.3);
7. deletes local `buddyai-*` backups older than `BACKUP_LOCAL_KEEP_DAYS` (default 14).

Without any of the new variables it behaves as before (daily local dump, 14 days), plus the integrity check
and the checksum file. A failure logs one line `BACKUP FAILED: <step>`:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs backup | grep -E "backup ok|FAILED|ERROR"
docker compose -f deploy/docker-compose.yml --env-file deploy/.env run --rm backup backup.sh   # one run now
```

The variables go in `deploy/.env` (Coolify: the resource's Environment Variables); all are optional and
listed in `deploy/.env.example`. Local backups stay on the same server, so on their own they do **not**
protect against losing the server: set up 8.1 and 8.2 before selling.

### 8.1 Encryption key (age), generated offline

Backups are encrypted with [age](https://age-encryption.org). Only the **public** key (recipient,
`age1…`) is on the server; the private key (identity) never is, so a stolen backup or a compromised
storage account reveals nothing.

1. On an **offline** machine with `age` installed: `age-keygen -o buddyai-backup-key.txt`. It prints
   `Public key: age1…`.
2. Store `buddyai-backup-key.txt` like the firmware signing key (`firmware/keys/README.md`): two encrypted
   copies (e.g. encrypted USB drives) in two different places, never in the repository, the server, CI,
   e-mail or chat. **Without it, encrypted backups cannot be restored.**
3. Put only the public key on the server: `BACKUP_AGE_RECIPIENT=age1…`. Several recipients
   (comma-separated) let a second key holder restore, and allow rotating keys: add the new key, wait until
   the old backups have expired, then remove the old one.

### 8.2 Off-site copy (rclone)

Any [rclone](https://rclone.org) backend works, e.g. a Hetzner Storage Box (SFTP, in another location
than the server) or an S3 bucket. Use credentials that can reach only the backup directory. Give the
container the rclone configuration in one of three ways (never commit it):

- **`deploy/backup.env`** (plain compose only, git-ignored, read if present): rclone's own environment
  variables, for a remote called `offsite`:

  ```
  RCLONE_CONFIG_OFFSITE_TYPE=sftp
  RCLONE_CONFIG_OFFSITE_HOST=<storage box host>
  RCLONE_CONFIG_OFFSITE_USER=<user>
  RCLONE_CONFIG_OFFSITE_PASS=<output of: rclone obscure 'the password'>
  ```

  (S3: `TYPE=s3`, `PROVIDER`, `ACCESS_KEY_ID`, `SECRET_ACCESS_KEY`, `REGION`, `ENDPOINT`.) Then
  `BACKUP_RCLONE_REMOTE=offsite:buddyai-backups` in `deploy/.env`. `rclone obscure` only hides the
  password from casual view; the file still is a secret.
- **`BACKUP_RCLONE_CONFIG_B64`** (works on Coolify too): a complete `rclone.conf` encoded with
  `base64 -w0 rclone.conf`. The container writes it to a private temp file for each run.
- **A mounted file** at `/root/.config/rclone/rclone.conf` (e.g. a `docker-compose.override.yml`).

Check with one run (command above): the log shows `uploaded off-site`, and `rclone ls offsite:buddyai-backups`
lists `.age` and `.sha256` files only.

### 8.3 serverdata (secret key, provider keys, firmware images)

The `serverdata` volume holds the cookie secret key, the provider keys entered in the web app and the
firmware images. It is mounted **read-only** into the backup container; `BACKUP_SERVERDATA=1` adds a
verified `tar.gz` to each run. It contains secrets, so like the dumps it leaves the server only
encrypted. Restore by hand: decrypt (`age -d -i key.txt -o sd.tar.gz <file>.age`), check the `.sha256`,
then unpack into the volume while the server is stopped.

### 8.4 Monthly restore test (separate database)

Once a month, and after any change to the backup setup, restore the latest off-site backup into a
**separate** database and record the date and result. `restore.sh` decrypts, checks the `.sha256` and the
archive, refuses the production database (unless `--force-production`), refuses a non-empty target
(unless `--clean`) and restores in one transaction. There is no default target.

On a trusted machine that holds the identity file (not the server):

```bash
rclone copy offsite:buddyai-backups ./restore-test --include 'buddyai-YYYYMMDD-HHMM.dump*'
docker build -t buddyai-backup deploy/backup
docker build -f deploy/postgres.Dockerfile -t buddyai-postgres .          # same database image (pgvector)
docker network create restore-test
docker run -d --name restore-db --network restore-test -e POSTGRES_USER=buddyai \
  -e POSTGRES_PASSWORD=<throwaway> -e POSTGRES_DB=buddyai_restore_test buddyai-postgres
docker run --rm -it --network restore-test -v "$PWD/restore-test:/restore:ro" -v /path/to/key-dir:/key:ro \
  buddyai-backup restore.sh --identity /key/buddyai-backup-key.txt \
  --target postgresql://buddyai:<throwaway>@restore-db:5432/buddyai_restore_test \
  /restore/buddyai-YYYYMMDD-HHMM.dump.age
```

It prints the table and account counts and the newest `audit_log` entry; compare them with production.
Then delete the test database, the container and the downloaded files (`docker rm -f restore-db`).
An unencrypted local dump can be tested on the server the same way, into a new database
(`docker compose … exec postgres createdb -U buddyai buddyai_restore_test`, then
`docker compose … run --rm backup restore.sh --target postgresql://buddyai:…@postgres:5432/buddyai_restore_test /backups/<file>.dump`).

### 8.5 Disaster recovery and deleted data

Restoring a backup brings back everything that was deleted after it was taken: deleted accounts,
erased conversation history. Those deletions must be applied again **before** the restored database
goes live.

1. Stop the server (`docker compose … stop server`) so nothing changes meanwhile.
2. Note the backup time (`restore.sh` prints `archive created at`).
3. If the old production database is still readable, export the deletions recorded after that time
   **before** overwriting it:

   ```sql
   \copy (select id, created_at, actor, action, account_id, device_id from audit_log
          where created_at >= '<backup time>' and action in ('account.delete', 'history.delete')
          order by id) to 'deletions.csv' csv header
   ```

   If it is gone, collect the deletions from the server's JSON logs and support records instead.
4. Restore (into a new database, or `--clean --force-production` into the old one).
5. Re-apply every deletion through the application's own deletion code, not hand-written SQL:
   `account.delete` → delete that account again (`app.accounts.delete_account`); `history.delete` →
   delete that device's conversation history again (operator app, or `ConversationRepo.delete_device_history`).
   A follow-up script can automate this step; it does not exist yet.
6. Only then start the server and Caddy.

Note: operator history deletion writes `history.delete` today; check that customer self-service deletions
(account, history) also write their audit events before relying on step 3. Data deleted on request
otherwise stays only in backups until they expire (local 14 days, off-site 30 days by default): use the
same periods in the privacy notice.

### 8.6 Coolify

Same service, same variables (set them in Coolify's Environment Variables; use
`BACKUP_RCLONE_CONFIG_B64` for the rclone configuration). Local backups are in the `backupdata` volume.
Coolify also has its own scheduled database backups to S3; check in your Coolify version whether they cover
a PostgreSQL that runs inside a compose stack like this one before relying on them instead.

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
- [ ] Backups encrypted and copied off-site (section 8.1–8.2), the age key stored offline in two places,
      and a restore test into a separate database done and recorded (8.4).
- [ ] Test purchase end-to-end in Stripe test mode, then one real purchase with live keys and a refund.
- [ ] Beta with 2–3 friendly customers before the public launch.
