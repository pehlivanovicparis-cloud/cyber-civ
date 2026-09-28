# CYBER CIV — Licensed Rental Backend

Metered API access to the CIV-GUIDE assistant. You hold the provider key
server-side; customers get their own `civ_…` keys, and every request is
authenticated, injected with the CIV-GUIDE system prompt + knowledge base,
and metered against the customer's monthly tier.

Stdlib only — no dependencies. SQLite for keys + usage.

## Quickstart (local)

```bash
export RENTAL_ADMIN_TOKEN="$(openssl rand -hex 32)"   # keep secret
export RENTAL_PROVIDER_KEY="sk-..."                   # your OpenAI-compatible key
# optional:
# export RENTAL_PROVIDER_BASE_URL="https://api.openai.com/v1"
# export RENTAL_MODEL="gpt-4o-mini"
# export RENTAL_PORT="8765"

python3 server.py
```

## Issuing a customer key

```bash
curl -s -X POST localhost:8765/admin/customers \
  -H "Authorization: Bearer $RENTAL_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Acme Corp","tier":"pro"}'
# -> {"id":1,"name":"Acme Corp","tier":"pro","monthly_limit":10000,
#     "api_key":"civ_..."}   # the key is shown ONCE; only its hash is stored
```

Tiers: `starter` (1,000 msgs/mo), `pro` (10,000), `enterprise` (unlimited).
Override with `"monthly_limit": N`.

## Customer usage

The API is OpenAI-compatible — point any client at it:

```bash
curl -s localhost:8765/v1/chat/completions \
  -H "Authorization: Bearer civ_..." \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"How does GOV work?"}]}'
```

The web chat at `docs/assistant.html` works unchanged: set its base URL to
your server and use a customer key instead of a provider key.

Customers can check their own quota: `GET /v1/usage`.

## Admin

```bash
GET  /admin/customers            # list (key prefixes only, never raw keys)
POST /admin/customers/<id>/revoke
GET  /admin/usage                # per-customer messages + tokens this month
```

## Production checklist

- Bind is `127.0.0.1` — put nginx or Caddy in front with TLS and forward to it.
- Run under systemd with `Restart=always`; keep `rental.db` on persistent disk.
- Set `RENTAL_RATE_LIMIT` (requests/minute per key, default 30).
- Rotate `RENTAL_ADMIN_TOKEN`; it is the only credential that can mint keys.
- Back up `rental.db` — it holds your customer registry and usage ledger.
- Run `stripe_webhook.py` (see `billing.md`) so subscriptions create and
  revoke keys automatically.

## Tests

```bash
python3 test_server.py          # 14 integration tests, stub provider
python3 test_stripe_webhook.py  # 17 webhook tests, fake admin server
```
