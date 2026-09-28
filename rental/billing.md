# Billing wiring (Stripe)

The server meters usage; `stripe_webhook.py` in this folder translates
Stripe subscription events into customer-key lifecycle calls. Card
processing, invoices, and tax live in Stripe — the rental backend never
sees a card number.

## Setup

1. **Stripe Dashboard → Products**: create recurring products —
   Starter $29/mo, Pro $99/mo. Copy each **Price ID** (`price_…`).
   Enterprise stays manual (custom quotes, invoicing).
2. **Developers → Webhooks → Add endpoint**: your public URL +
   `/stripe-webhook`, subscribed to `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted`.
   Copy the **signing secret** (`whsec_…`).
3. **Run the receiver**:
   ```bash
   export STRIPE_WEBHOOK_SECRET="whsec_..."
   export RENTAL_ADMIN_URL="http://127.0.0.1:8765"
   export RENTAL_ADMIN_TOKEN="..."      # same token as the rental server
   export PRICE_STARTER="price_..."     # Starter price ID
   export PRICE_PRO="price_..."         # Pro price ID
   # optional: SMTP_HOST/PORT/USER/PASS/FROM to email keys automatically
   python3 stripe_webhook.py           # listens on 127.0.0.1:8766
   ```
   Expose it over HTTPS (reverse proxy) and register that public URL in
   Stripe. Without SMTP configured, new API keys print to stdout for
   manual delivery.
4. **Checkout**: use Stripe Payment Links (no code) or Checkout Sessions.
   Customer pays → Stripe fires the webhook → key is minted and delivered.

## Event mapping

| Stripe event | Action |
|---|---|
| `customer.subscription.created` | `POST /admin/customers` with the tier matching the Price ID; the returned `api_key` is emailed once (shown once, stored as hash) |
| `customer.subscription.updated` (plan change) | revoke old key, mint new key at the new tier |
| `customer.subscription.deleted` | revoke the key |
| `invoice.payment_failed` | logged only — key stays alive during dunning; it dies only when the subscription is deleted |

Signatures are verified (HMAC-SHA256 + timestamp window); retried
deliveries are idempotent via the event log in `webhook.db`.

## Tests

```bash
python3 test_stripe_webhook.py   # 17 tests: signature, idempotency,
                                # provisioning, upgrades, cancellation
```

## Suggested tiers

| Tier | Price | Monthly messages |
|---|---|---|
| Starter | $29/mo | 1,000 |
| Pro | $99/mo | 10,000 |
| Enterprise | custom | unlimited |
