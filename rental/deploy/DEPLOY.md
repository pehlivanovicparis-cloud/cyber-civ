# Deploy runbook — CYBER CIV rental backend + Stripe webhook

Target: any Linux VPS (Ubuntu 22.04+). Needs: a domain (or subdomain)
pointing at the server, e.g. `api.yourdomain.com`.

## 1. Server prep (once)

```bash
sudo useradd -r -m -s /usr/sbin/nologin cyberciv
sudo mkdir -p /opt/cyberciv/rental
sudo cp -r rental/* /opt/cyberciv/rental/
sudo chown -R cyberciv:cyberciv /opt/cyberciv
```

Python 3.10+ stdlib only — no pip packages needed.

## 2. Secrets

```bash
cp rental/deploy/env.example /opt/cyberciv/rental.env
# edit /opt/cyberciv/rental.env:
#   RENTAL_ADMIN_TOKEN  -> output of: openssl rand -hex 32
#   RENTAL_PROVIDER_KEY -> your OpenAI-compatible API key
#   STRIPE_WEBHOOK_SECRET -> whsec_... (see step 4)
#   SMTP_*             -> optional, for automatic key emails
sudo chmod 600 /opt/cyberciv/rental.env
sudo chown cyberciv:cyberciv /opt/cyberciv/rental.env
```

## 3. Services

```bash
sudo cp rental/deploy/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cyberciv-rental cyberciv-webhook
curl -s http://127.0.0.1:8765/healthz   # {"ok": true} via server.py
curl -s http://127.0.0.1:8766/healthz   # {"ok": true}
```

## 4. HTTPS + Stripe webhook

```bash
# install Caddy: https://caddyserver.com/docs/install
# edit rental/deploy/Caddyfile -> your domain, then:
sudo cp rental/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Stripe Dashboard → Developers → Webhooks → Add endpoint:
- URL: `https://api.yourdomain.com/stripe-webhook`
- Events: `customer.subscription.created`, `customer.subscription.updated`,
  `customer.subscription.deleted`
- Copy the signing secret (`whsec_…`) into `rental.env`, then
  `sudo systemctl restart cyberciv-webhook`

Test from the Dashboard with "Send test webhook".

## 5. Backups

`rental.db` (customer registry + usage) and `webhook.db` (event log + links)
live in `/opt/cyberciv/rental/`. Back them up nightly, e.g.:

```bash
0 3 * * * sqlite3 /opt/cyberciv/rental/rental.db ".backup '/backups/rental-$(date +\%F).db'"
0 3 * * * sqlite3 /opt/cyberciv/rental/webhook.db ".backup '/backups/webhook-$(date +\%F).db'"
```

## 6. Ops notes

- Key delivery: with SMTP set, customers get their key by email
  automatically. Without it, keys print to the webhook's journal
  (`journalctl -u cyberciv-webhook`) — deliver manually, then consider
  adding SMTP.
- Email failure never blocks provisioning: the key is minted and linked;
  re-issue via the admin API if a customer reports a missing email.
- Webhook retries: if the rental server is down, the receiver answers 500
  and Stripe retries; the retry provisions exactly once (idempotent via
  the links table). No manual cleanup needed.
- Logs: `journalctl -u cyberciv-rental -f` / `journalctl -u cyberciv-webhook -f`
