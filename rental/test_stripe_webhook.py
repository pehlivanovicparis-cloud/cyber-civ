#!/usr/bin/env python3
"""Tests for the Stripe webhook receiver.

Fake rental admin server + Stripe-style signed payloads. No network, no keys.

Usage: python3 test_stripe_webhook.py
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ADMIN_CALLS = []  # (method, path, body) seen by the fake admin server
NEXT_ID = [100]


class FakeAdmin(BaseHTTPRequestHandler):
    def _json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(length).decode()) if length else {}
        ADMIN_CALLS.append((self.path, body))
        if self.path == "/admin/customers":
            NEXT_ID[0] += 1
            return self._json(201, {
                "id": NEXT_ID[0], "name": body.get("name"),
                "tier": body.get("tier"), "api_key": "civ_testkey123"})
        if self.path.endswith("/revoke"):
            return self._json(200, {"revoked": True})
        return self._json(404, {})

    def log_message(self, *a):
        pass


def sign(payload: bytes, secret: str, ts: int | None = None) -> str:
    ts = ts if ts is not None else int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload,
                   hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def post_webhook(port, event, secret, ts=None):
    payload = json.dumps(event).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/stripe-webhook",
        data=payload, method="POST",
        headers={"Stripe-Signature": sign(payload, secret, ts)})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def subscription(sub_id, customer, price_id, email="buyer@example.com"):
    return {
        "id": sub_id, "customer": customer, "customer_email": email,
        "items": {"data": [{"price": {"id": price_id}}]},
    }


def main():
    tmp = tempfile.mkdtemp()
    admin_srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeAdmin)
    admin_port = admin_srv.server_address[1]
    threading.Thread(target=admin_srv.serve_forever, daemon=True).start()

    hook_port = admin_port + 1  # just another port; find free properly below
    s = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    hook_port = s.server_address[1]
    s.server_close()

    secret = "whsec_test123"
    os.environ.update({
        "STRIPE_WEBHOOK_SECRET": secret,
        "RENTAL_ADMIN_URL": f"http://127.0.0.1:{admin_port}",
        "RENTAL_ADMIN_TOKEN": "test-admin",
        "PRICE_STARTER": "price_starter_1",
        "PRICE_PRO": "price_pro_1",
        "WEBHOOK_PORT": str(hook_port),
        "WEBHOOK_DB": os.path.join(tmp, "hook.db"),
    })

    import stripe_webhook as hook
    hook.init_db()
    srv = ThreadingHTTPServer(("127.0.0.1", hook_port), hook.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    passed = []
    def check(name, cond):
        assert cond, f"FAILED: {name}"
        passed.append(name)

    # 1. subscription.created -> key minted at starter tier
    ev = {"id": "evt_1", "type": "customer.subscription.created",
          "data": {"object": subscription("sub_1", "cus_1", "price_starter_1")}}
    status, _ = post_webhook(hook_port, ev, secret)
    check("created -> 200", status == 200)
    check("admin create called",
          ADMIN_CALLS[-1] == ("/admin/customers",
                              {"name": "buyer@example.com", "tier": "starter"}))
    check("link stored", hook.get_link("cus_1")["tier"] == "starter")

    # 2. duplicate delivery -> idempotent
    n = len(ADMIN_CALLS)
    status, body = post_webhook(hook_port, ev, secret)
    check("duplicate -> 200 + flagged",
          status == 200 and body.get("duplicate") is True)
    check("no second create", len(ADMIN_CALLS) == n)

    # 3. bad signature -> 400, nothing happens
    n = len(ADMIN_CALLS)
    ev2 = {"id": "evt_2", "type": "customer.subscription.created",
           "data": {"object": subscription("sub_2", "cus_2", "price_pro_1")}}
    payload = json.dumps(ev2).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{hook_port}/stripe-webhook", data=payload,
        method="POST", headers={"Stripe-Signature": sign(payload, "wrong")})
    try:
        urllib.request.urlopen(req, timeout=10)
        status = 200
    except urllib.error.HTTPError as e:
        status = e.code
    check("bad signature -> 400", status == 400)
    check("no admin call on bad sig", len(ADMIN_CALLS) == n)

    # 4. stale timestamp -> 400
    req = urllib.request.Request(
        f"http://127.0.0.1:{hook_port}/stripe-webhook", data=payload,
        method="POST",
        headers={"Stripe-Signature": sign(payload, secret,
                                          ts=int(time.time()) - 9999)})
    try:
        urllib.request.urlopen(req, timeout=10)
        status = 200
    except urllib.error.HTTPError as e:
        status = e.code
    check("stale timestamp -> 400", status == 400)

    # 5. plan upgrade -> revoke old, mint new at pro
    ev3 = {"id": "evt_3", "type": "customer.subscription.updated",
           "data": {"object": subscription("sub_1", "cus_1", "price_pro_1")}}
    n = len(ADMIN_CALLS)
    status, _ = post_webhook(hook_port, ev3, secret)
    check("updated -> 200", status == 200)
    check("revoke + recreate",
          len(ADMIN_CALLS) == n + 2
          and ADMIN_CALLS[-2][0].endswith("/revoke")
          and ADMIN_CALLS[-1][1]["tier"] == "pro")
    check("link updated", hook.get_link("cus_1")["tier"] == "pro")

    # 6. update with no plan change -> no-op
    n = len(ADMIN_CALLS)
    ev4 = {"id": "evt_4", "type": "customer.subscription.updated",
           "data": {"object": subscription("sub_1", "cus_1", "price_pro_1")}}
    post_webhook(hook_port, ev4, secret)
    check("no-op update", len(ADMIN_CALLS) == n)

    # 7. cancellation -> revoke
    ev5 = {"id": "evt_5", "type": "customer.subscription.deleted",
           "data": {"object": subscription("sub_1", "cus_1", "price_pro_1")}}
    n = len(ADMIN_CALLS)
    status, _ = post_webhook(hook_port, ev5, secret)
    check("deleted -> 200", status == 200)
    check("revoke called",
          len(ADMIN_CALLS) == n + 1 and ADMIN_CALLS[-1][0].endswith("/revoke"))
    check("link marked revoked", hook.get_link("cus_1")["revoked"] == 1)

    # 8. unknown price -> 200, no provisioning (config error, not retryable)
    n = len(ADMIN_CALLS)
    ev6 = {"id": "evt_6", "type": "customer.subscription.created",
           "data": {"object": subscription("sub_9", "cus_9", "price_unknown")}}
    status, _ = post_webhook(hook_port, ev6, secret)
    check("unknown price -> 200 no provision",
          status == 200 and len(ADMIN_CALLS) == n)

    # 9. payment_failed -> 200, key left alone
    n = len(ADMIN_CALLS)
    ev7 = {"id": "evt_7", "type": "invoice.payment_failed",
           "data": {"object": {"customer": "cus_1"}}}
    status, _ = post_webhook(hook_port, ev7, secret)
    check("payment_failed tolerated",
          status == 200 and len(ADMIN_CALLS) == n)

    print(f"ALL {len(passed)} TESTS PASSED")
    srv.shutdown()
    admin_srv.shutdown()


if __name__ == "__main__":
    main()
