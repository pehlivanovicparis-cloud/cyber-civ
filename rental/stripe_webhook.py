#!/usr/bin/env python3
"""
CYBER CIV — Stripe webhook receiver.

Translates Stripe subscription events into rental-server customer-key
lifecycle calls:

  customer.subscription.created -> mint a customer key, email it once
  customer.subscription.updated -> plan changed: revoke old key, mint new
  customer.subscription.deleted -> revoke the key
  invoice.payment_failed       -> logged only (Stripe dunning retries;
                                  key dies only when the subscription does)

Stripe signatures are verified (HMAC-SHA256, timestamp tolerance).
Event IDs are claimed before processing and marked done after, so retried
deliveries are idempotent: a failed run leaves the claim open and Stripe's
retry reprocesses it, while the links table prevents double-provisioning.

Stdlib only. No dependencies.

Environment
-----------
STRIPE_WEBHOOK_SECRET  whsec_... from the Stripe Dashboard (required)
RENTAL_ADMIN_URL       e.g. http://127.0.0.1:8765 (required)
RENTAL_ADMIN_TOKEN     admin token for the rental server (required)
PRICE_STARTER          Stripe Price ID for the Starter tier (required)
PRICE_PRO              Stripe Price ID for the Pro tier (required)
WEBHOOK_PORT           listen port (default 8766)
WEBHOOK_DB             sqlite path (default webhook.db next to this file)
SMTP_HOST/PORT/USER/PASS/FROM  optional; without these the new API key is
                       printed to stdout for manual delivery instead of emailed

Copyright (c) 2026 Paris Pehlivanovic. All rights reserved.
PROPRIETARY — see LICENSE for terms.
"""

import hashlib
import hmac
import json
import os
import smtplib
import sqlite3
import threading
import time
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

HERE = Path(__file__).resolve().parent


def env(name, default=None, required=False):
    v = os.environ.get(name, default)
    if required and not v:
        raise SystemExit(f"{name} is required")
    return v


WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", required=True)
ADMIN_URL = env("RENTAL_ADMIN_URL", required=True).rstrip("/")
ADMIN_TOKEN = env("RENTAL_ADMIN_TOKEN", required=True)
PORT = int(env("WEBHOOK_PORT", "8766"))
DB_PATH = Path(env("WEBHOOK_DB", str(HERE / "webhook.db")))
TOLERANCE = 300  # seconds; Stripe replay-protection window
STALE_AFTER = 600  # seconds; abandoned 'processing' claims may be reclaimed

PRICE_TO_TIER = {
    env("PRICE_STARTER", required=True): "starter",
    env("PRICE_PRO", required=True): "pro",
}

SMTP = {
    "host": os.environ.get("SMTP_HOST"),
    "port": int(os.environ.get("SMTP_PORT", "587")),
    "user": os.environ.get("SMTP_USER"),
    "password": os.environ.get("SMTP_PASS"),
    "from": os.environ.get("SMTP_FROM"),
}

# ---------------------------------------------------------------------------
# Database: event log (idempotency) + stripe->rental customer map
# ---------------------------------------------------------------------------

_lock = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock, db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'processing',
                received_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS links (
                stripe_customer_id TEXT PRIMARY KEY,
                rental_customer_id INTEGER NOT NULL,
                tier TEXT NOT NULL,
                email TEXT,
                revoked INTEGER NOT NULL DEFAULT 0
            );
        """)
        # migrate dev DBs created before the status column existed
        cols = {r["name"]
                for r in conn.execute("PRAGMA table_info(events)")}
        if "status" not in cols:
            conn.execute("ALTER TABLE events "
                         "ADD COLUMN status TEXT NOT NULL DEFAULT 'done'")


def claim_event(event_id: str, etype: str) -> str:
    """Claim an event for processing.

    Returns 'new' (caller should process it), 'duplicate' (already done),
    or 'in-progress' (another worker holds a fresh claim; ack without
    reprocessing). A 'processing' claim older than STALE_AFTER seconds is
    treated as abandoned (e.g. crash mid-handling) and may be reclaimed.
    """
    now = int(time.time())
    with _lock, db() as conn:
        row = conn.execute(
            "SELECT status, received_at FROM events WHERE event_id = ?",
            (event_id,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO events (event_id, type, status, received_at)"
                " VALUES (?, ?, 'processing', ?)",
                (event_id, etype, now))
            return "new"
        if row["status"] == "done":
            return "duplicate"
        try:
            age = now - int(row["received_at"])
        except (TypeError, ValueError):
            age = 0
        if age >= STALE_AFTER:
            conn.execute(
                "UPDATE events SET status='processing', received_at=?,"
                " type=? WHERE event_id = ?",
                (now, etype, event_id))
            return "new"
        return "in-progress"


def mark_done(event_id: str):
    with _lock, db() as conn:
        conn.execute("UPDATE events SET status='done' WHERE event_id = ?",
                     (event_id,))


def get_link(stripe_cid: str):
    with _lock, db() as conn:
        return conn.execute(
            "SELECT * FROM links WHERE stripe_customer_id = ?",
            (stripe_cid,)).fetchone()


def save_link(stripe_cid: str, rental_id: int, tier: str, email: str | None):
    with _lock, db() as conn:
        conn.execute(
            """INSERT INTO links
                   (stripe_customer_id, rental_customer_id, tier, email, revoked)
               VALUES (?,?,?,?,0)
               ON CONFLICT(stripe_customer_id) DO UPDATE SET
                   rental_customer_id=excluded.rental_customer_id,
                   tier=excluded.tier, email=excluded.email, revoked=0""",
            (stripe_cid, rental_id, tier, email))


def mark_revoked(stripe_cid: str):
    with _lock, db() as conn:
        conn.execute("UPDATE links SET revoked = 1 WHERE stripe_customer_id = ?",
                     (stripe_cid,))


# ---------------------------------------------------------------------------
# Rental admin API client
# ---------------------------------------------------------------------------

class AdminError(Exception):
    pass


def admin(method: str, path: str, body: dict | None = None) -> dict:
    req = Request(
        ADMIN_URL + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}",
                 "Content-Type": "application/json"},
        method=method)
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        raise AdminError(f"admin {method} {path}: HTTP {e.code}")
    except URLError as e:
        raise AdminError(f"admin {method} {path}: unreachable ({e.reason})")


# ---------------------------------------------------------------------------
# Email (or manual fallback)
# ---------------------------------------------------------------------------

WELCOME = """Your CYBER CIV assistant access is ready.

API key: {key}
Tier: {tier} ({limit} messages/month)
Endpoint: {endpoint}/v1/chat/completions

Keep this key secret — it is shown once and stored only as a hash.
Questions: Pehlivanovicparis@gmail.com
"""

TIER_LIMITS = {"starter": "1,000", "pro": "10,000", "enterprise": "unlimited"}


def deliver_key(email: str | None, key: str, tier: str):
    body = WELCOME.format(key=key, tier=tier,
                          limit=TIER_LIMITS.get(tier, "?"),
                          endpoint=ADMIN_URL)
    if SMTP["host"] and SMTP["user"] and email:
        msg = EmailMessage()
        msg["From"] = SMTP["from"] or SMTP["user"]
        msg["To"] = email
        msg["Subject"] = "Your CYBER CIV API key"
        msg.set_content(body)
        with smtplib.SMTP(SMTP["host"], SMTP["port"]) as s:
            s.starttls()
            s.login(SMTP["user"], SMTP["password"])
            s.send_message(msg)
        print(f"[webhook] key emailed to {email}")
    else:
        print("=" * 60)
        print("[webhook] SMTP not configured — deliver this key manually:")
        print(body)
        print("=" * 60)


# ---------------------------------------------------------------------------
# Stripe signature verification
# ---------------------------------------------------------------------------

def verify_signature(payload: bytes, header: str) -> bool:
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["t"])
    except Exception:
        return False
    if abs(time.time() - ts) > TOLERANCE:
        return False
    signed = f"{ts}.".encode() + payload
    expected = hmac.new(WEBHOOK_SECRET.encode(), signed,
                        hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig)
               for k, sig in parts.items() if k == "v1")


# ---------------------------------------------------------------------------
# Event handling
# ---------------------------------------------------------------------------

def price_to_tier(subscription: dict) -> str | None:
    items = subscription.get("items", {}).get("data", [])
    if not items:
        return None
    price_id = items[0].get("price", {}).get("id")
    return PRICE_TO_TIER.get(price_id)


def handle_event(event: dict):
    etype = event.get("type", "")
    obj = event.get("data", {}).get("object", {})

    if etype == "customer.subscription.created":
        stripe_cid = obj.get("customer")
        tier = price_to_tier(obj)
        if tier is None:
            print(f"[webhook] unknown price in {event['id']} — "
                  "check PRICE_STARTER/PRICE_PRO")
            return
        if get_link(stripe_cid) and not get_link(stripe_cid)["revoked"]:
            print(f"[webhook] {stripe_cid} already provisioned, skipping")
            return
        email = obj.get("customer_email") or None
        cust = admin("POST", "/admin/customers",
                     {"name": email or stripe_cid, "tier": tier})
        save_link(stripe_cid, cust["id"], tier, email)
        try:
            deliver_key(email, cust["api_key"], tier)
        except Exception as e:
            # Key is minted and linked; don't fail the event over email.
            print(f"[webhook] DELIVERY FAILED for {stripe_cid}: {e!r} — "
                  f"key minted (rental id {cust['id']}); re-issue manually")
        print(f"[webhook] provisioned {tier} key for {stripe_cid}")

    elif etype == "customer.subscription.updated":
        stripe_cid = obj.get("customer")
        tier = price_to_tier(obj)
        link = get_link(stripe_cid)
        if tier is None or link is None or link["revoked"]:
            return
        if tier == link["tier"]:
            return  # quantity/renewal change, nothing to do
        admin("POST", f"/admin/customers/{link['rental_customer_id']}/revoke")
        cust = admin("POST", "/admin/customers",
                     {"name": link["email"] or stripe_cid, "tier": tier})
        save_link(stripe_cid, cust["id"], tier, link["email"])
        try:
            deliver_key(link["email"], cust["api_key"], tier)
        except Exception as e:
            print(f"[webhook] DELIVERY FAILED for {stripe_cid}: {e!r} — "
                  f"key minted (rental id {cust['id']}); re-issue manually")
        print(f"[webhook] {stripe_cid} moved {link['tier']} -> {tier}")

    elif etype == "customer.subscription.deleted":
        stripe_cid = obj.get("customer")
        link = get_link(stripe_cid)
        if link is None or link["revoked"]:
            return
        admin("POST", f"/admin/customers/{link['rental_customer_id']}/revoke")
        mark_revoked(stripe_cid)
        print(f"[webhook] revoked key for cancelled {stripe_cid}")

    elif etype == "invoice.payment_failed":
        print(f"[webhook] payment failed for "
              f"{obj.get('customer')} — leaving key active during dunning")

    else:
        print(f"[webhook] ignoring event type {etype}")


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "CIVWebhook/1.0"

    def _send(self, status: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/healthz":
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/stripe-webhook":
            return self._send(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length", 0) or 0)
        payload = self.rfile.read(length)
        sig = self.headers.get("Stripe-Signature", "")
        if not verify_signature(payload, sig):
            return self._send(400, {"error": "invalid signature"})
        try:
            event = json.loads(payload.decode())
        except Exception:
            return self._send(400, {"error": "invalid JSON"})
        event_id = event.get("id", "")
        if not event_id:
            return self._send(400, {"error": "missing event id"})
        claim = claim_event(event_id, event.get("type", ""))
        if claim == "duplicate":
            return self._send(200, {"received": True, "duplicate": True})
        if claim == "in-progress":
            return self._send(200, {"received": True, "in_progress": True})
        try:
            handle_event(event)
        except AdminError as e:
            # 500 so Stripe retries. The event is NOT marked done, so the
            # retry reprocesses it; the links table keeps provisioning
            # idempotent (no double keys).
            print(f"[webhook] admin error on {event_id}: {e} — leaving "
                  "unacked for Stripe retry")
            return self._send(500, {"error": "admin backend unavailable"})
        except Exception as e:  # unexpected bug: log loudly, but ack so a
            # poison event doesn't retry forever
            print(f"[webhook] UNHANDLED ERROR on {event_id}: {e!r}")
        mark_done(event_id)
        return self._send(200, {"received": True})


def main():
    init_db()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Stripe webhook receiver on 127.0.0.1:{PORT}/stripe-webhook")
    print("Expose it via HTTPS (reverse proxy) and register the public URL "
          "in the Stripe Dashboard.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
