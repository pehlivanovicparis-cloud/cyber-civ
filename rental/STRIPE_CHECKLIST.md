# Stripe — info needed (checklist)

Everything the landing page and webhook receiver need from the Stripe
Dashboard. Fill in each value as you go; nothing here is secret except
the keys marked 🔑 (keep those server-side only).

## 1. Account
- [ ] Signed up at https://dashboard.stripe.com/register
- [ ] Business profile completed (Settings → Business settings)
- [ ] Bank account added for payouts (Settings → Payouts)

## 2. Products & prices (Product catalog → Add product)
- [ ] **Starter** — $29.00 USD, recurring, monthly
      Price ID: `________________________________________`
- [ ] **Pro** — $99.00 USD, recurring, monthly
      Price ID: `________________________________________`

## 3. Payment Links (for the pricing page buttons)
Create one per product: product page → **Buy button → Payment Links** →
Create payment link.
- [ ] Starter Payment Link URL: `________________________________________`
- [ ] Pro Payment Link URL: `________________________________________`
      → send these two URLs to Cosmo; the pricing page buttons get wired up.

## 4. Webhook (Developers → Webhooks → Add endpoint)
Endpoint URL: `https://YOUR-DOMAIN/stripe-webhook` (after deploying the receiver)
Events:
- [ ] `customer.subscription.created`
- [ ] `customer.subscription.updated`
- [ ] `customer.subscription.deleted`
- [ ] Signing secret 🔑: `whsec_________________________________________`

## 5. Deploy-time environment (never in chat, never in git)
```bash
export STRIPE_WEBHOOK_SECRET="whsec_..."   # 🔑 from step 4
export PRICE_STARTER="price_..."           # from step 2
export PRICE_PRO="price_..."               # from step 2
export RENTAL_ADMIN_URL="http://127.0.0.1:8765"
export RENTAL_ADMIN_TOKEN="..."            # 🔑 same token as rental server
# optional, to email keys automatically:
export SMTP_HOST="..." SMTP_PORT="587" SMTP_USER="..." SMTP_PASS="..." SMTP_FROM="..."
```

## 6. Testing before real money
- [ ] Flip the Dashboard to **Test mode** (top right)
- [ ] Repeat steps 2–4 in test mode (test price IDs start with `price_`, test
      secrets with `whsec_` — same shape, no real charges)
- [ ] Pay with test card `4242 4242 4242 4242`, any future expiry, any CVC
- [ ] Confirm: key minted → key emailed/printed → chat call works → cancel
      subscription → key revoked
- [ ] Switch to **Live mode** and redo steps 2–4 with live values
