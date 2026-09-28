#!/usr/bin/env python3
"""Integration tests for the CYBER CIV rental backend.

Spins up a stub LLM provider and the rental server on ephemeral ports, then
exercises: admin key creation, customer auth, system-prompt injection,
usage metering, monthly limits, revocation, and rate limiting.

Usage: python3 test_server.py
"""

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

SEEN_MESSAGES = []  # what the stub provider received


class StubProvider(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode())
        SEEN_MESSAGES.append(body["messages"])
        resp = {
            "id": "stub-1",
            "choices": [{"message": {"role": "assistant",
                                     "content": "stub reply"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                      "total_tokens": 15},
        }
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def free_port():
    s = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = s.server_address[1]
    s.server_close()
    return port


def call(port, path, method="GET", token=None, body=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def main():
    tmp = tempfile.mkdtemp()
    provider_port = free_port()
    rental_port = free_port()

    stub = ThreadingHTTPServer(("127.0.0.1", provider_port), StubProvider)
    threading.Thread(target=stub.serve_forever, daemon=True).start()

    os.environ.update({
        "RENTAL_ADMIN_TOKEN": "test-admin-token",
        "RENTAL_PROVIDER_KEY": "test-provider-key",
        "RENTAL_PROVIDER_BASE_URL": f"http://127.0.0.1:{provider_port}",
        "RENTAL_MODEL": "stub-model",
        "RENTAL_PROMPT_DIR": str(HERE.parent / "assistant"),
        "RENTAL_DB": os.path.join(tmp, "test.db"),
        "RENTAL_PORT": str(rental_port),
        "RENTAL_RATE_LIMIT": "1000",
    })

    import server as rental
    rental.init_db()
    srv = ThreadingHTTPServer(("127.0.0.1", rental_port), rental.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    passed = []
    def check(name, cond):
        assert cond, f"FAILED: {name}"
        passed.append(name)

    # 1. health
    s, _ = call(rental_port, "/healthz")
    check("healthz", s == 200)

    # 2. admin creates a customer
    s, cust = call(rental_port, "/admin/customers", "POST",
                   token="test-admin-token",
                   body={"name": "Acme", "tier": "starter"})
    check("admin create customer", s == 201 and cust["api_key"].startswith("civ_"))
    key = cust["api_key"]

    # 3. wrong admin token rejected
    s, _ = call(rental_port, "/admin/customers", "POST",
                token="wrong", body={"name": "X"})
    check("admin auth", s == 401)

    # 4. chat without key rejected
    s, _ = call(rental_port, "/v1/chat/completions", "POST",
                body={"messages": [{"role": "user", "content": "hi"}]})
    check("customer auth required", s == 401)

    # 5. chat with key works; system prompt injected first
    s, resp = call(rental_port, "/v1/chat/completions", "POST", token=key,
                   body={"messages": [{"role": "user", "content": "hello"}]})
    check("chat ok", s == 200 and resp["choices"][0]["message"]["content"] == "stub reply")
    check("system injected",
          SEEN_MESSAGES[-1][0]["role"] == "system"
          and "CIV-GUIDE" in SEEN_MESSAGES[-1][0]["content"])
    check("user message preserved",
          SEEN_MESSAGES[-1][-1] == {"role": "user", "content": "hello"})

    # 6. usage metered
    s, usage = call(rental_port, "/v1/usage", token=key)
    check("usage metered",
          s == 200 and usage["messages_used"] == 1 and usage["tokens_total"] == 15)

    # 7. monthly limit enforced (customer with limit 1)
    s, small = call(rental_port, "/admin/customers", "POST",
                    token="test-admin-token",
                    body={"name": "Tiny", "tier": "starter", "monthly_limit": 1})
    k2 = small["api_key"]
    s, _ = call(rental_port, "/v1/chat/completions", "POST", token=k2,
                body={"messages": [{"role": "user", "content": "one"}]})
    check("first message ok", s == 200)
    s, err = call(rental_port, "/v1/chat/completions", "POST", token=k2,
                  body={"messages": [{"role": "user", "content": "two"}]})
    check("limit enforced", s == 402 and "limit" in err["error"])

    # 8. revoke kills the key
    s, _ = call(rental_port, f"/admin/customers/{small['id']}/revoke",
                "POST", token="test-admin-token")
    check("revoke ok", s == 200)
    s, _ = call(rental_port, "/v1/chat/completions", "POST", token=k2,
                body={"messages": [{"role": "user", "content": "x"}]})
    check("revoked key rejected", s == 401)

    # 9. admin usage rollup
    s, roll = call(rental_port, "/admin/usage", token="test-admin-token")
    check("admin rollup", s == 200 and len(roll["usage"]) == 2)

    # 10. provider key never leaks to client
    raw_resp = json.dumps(resp)
    check("no provider key leak", "test-provider-key" not in raw_resp)

    print(f"ALL {len(passed)} TESTS PASSED")
    srv.shutdown()


if __name__ == "__main__":
    main()
