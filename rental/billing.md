# Billing wiring (Stripe example)

The server meters usage but does not charge money itself. The intended
production setup is: Stripe handles subscriptions; a small webhook receiver
translates subscription events into customer-key lifecycle calls.

## Suggested tiers

| Tier | Price | Monthly messages |
|---|---|---|
| Starter | $29/mo | 1,000 |
| Pro | $99/mo | 10,000 |
| Enterprise | custom | unlimited |

## Webhook mapping

Run a tiny HTTPS endpoint (e.g. Stripe CLI / a 30-line Flask app — your
choice) subscribed to these events, each translating to one admin call on
the rental server (`Authorization: Bearer $RENTAL_ADMIN_TOKEN`):

| Stripe event | Action |
|---|---|
| `customer.subscription.created` | `POST /admin/customers` with `{"name": <customer name>, "tier": <price→tier map>}`; email the returned `api_key` to the customer **once** |
| `customer.subscription.deleted` | `POST /admin/customers/<id>/revoke` |
| `customer.subscription.updated` (plan change) | revoke old, create new with the new tier |

Keep a local map of `stripe_customer_id → rental customer id` in the
webhook receiver so renewals and cancellations hit the right key.

## Dunning / over-quota

- Over-quota responses are HTTP 402 with the tier named — your client UI
  should surface an upgrade prompt, not a raw error.
- Failed payments: Stripe retries per your dunning settings; revoke the key
  only on `customer.subscription.deleted`, not on the first failed invoice.

## What is NOT built here

Actual card processing, invoices, and tax live in Stripe (or Paddle/Lemon
Squeezy). This keeps PCI scope off your server entirely: the rental backend
never sees a card number.
