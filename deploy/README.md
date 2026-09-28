# Going live: server runbook

This takes BuddyAI from your PC to a rented cloud server with HTTPS, PostgreSQL and daily backups.
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

1. Create an account at <https://console.hetzner.cloud>, then a project called "BuddyAI".
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

## 6. Stripe (shop + BuddyAI Care subscription)

Everything below is done in **test mode** first. Switch to live mode only after a full test purchase.

1. **Account:** create it, then complete your business details and bank account.
2. **Products** (Product catalogue):
   - **"BuddyAI Watch"**: a *one-off* price in GBP and one in EUR.
   - **"BuddyAI Care"**: a *recurring monthly* price in GBP and one in EUR. The 90-day trial is added
     by the server at checkout (`BUDDYAI_CARE_TRIAL_DAYS`).
   - Copy the four `price_…` ids into `deploy/.env`.
3. **Tax:** enable Stripe Tax and add your registrations: UK VAT, and **EU OSS** for EU consumers.
   Mark prices as tax-inclusive or exclusive, consistently with the website.
4. **Shipping:** create shipping rates (e.g. "UK standard", "EU standard") in GBP and EUR and put
   their `shr_…` ids in `.env`.
5. **Checkout settings:**
   - set your Terms of Service and Privacy Policy URLs (`https://www.<domain>/legal/...`);
   - the server requires customers to accept the terms.
6. **Customer portal:** enable it, allowing customers to update cards, see invoices and cancel.
7. **Webhook:**
   - add the endpoint `https://app.<domain>/api/stripe/webhook`;
   - select the events `checkout.session.completed`, `customer.subscription.created`,
     `customer.subscription.updated`, `customer.subscription.deleted`,
     `customer.subscription.trial_will_end` (trial reminder email), `invoice.paid`,
     `invoice.payment_failed` and `charge.refunded`;
   - copy the signing secret into `BUDDYAI_STRIPE_WEBHOOK_SECRET`.
8. Restart the server:
   `docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d server`.
9. **Test purchase:** on the website click **Buy now** and pay with card `4242 4242 4242 4242`.
   Then check that:
   - an order appears under **Admin → Orders**;
   - a customer account appears under **Customers**, with a "set your password" email;
   - the subscription status shows "trialing".
10. Repeat with live keys when everything works.

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

## 9. Before selling: non-code checklist

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
