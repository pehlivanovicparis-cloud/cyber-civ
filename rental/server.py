#!/usr/bin/env python3
"""
CYBER CIV — licensed rental backend.

A metered API proxy: customers get their own API keys, every chat request is
authenticated, injected with the CIV-GUIDE system prompt + knowledge base,
forwarded to the upstream LLM provider (key held server-side only), and
metered against the customer's monthly tier limit.

Stdlib only. No dependencies.

Endpoints
---------
GET  /healthz                  -> {"ok": true}
POST /v1/chat/completions      -> customer auth; OpenAI-compatible proxy
GET  /v1/usage                  -> customer auth; own usage vs tier limit
POST /admin/customers           -> admin auth; {"name","tier","monthly_limit"?}
GET  /admin/customers           -> admin auth; list customers (no raw keys)
POST /admin/customers/<id>/revoke -> admin auth
GET  /admin/usage               -> admin auth; totals per customer

Environment
-----------
RENTAL_ADMIN_TOKEN       admin bearer token (required)
RENTAL_PROVIDER_KEY      upstream LLM API key (required, never leaves server)
RENTAL_PROVIDER_BASE_URL upstream base URL (default https://api.openai.com/v1)
RENTAL_MODEL             default model (default gpt-4o-mini)
RENTAL_PROMPT_DIR        dir with SYSTEM_PROMPT.md + KNOWLEDGE.md
                         (default ../assistant relative to this file)
RENTAL_DB                sqlite path (default rental.db next to this file)
RENTAL_PORT              listen port (default 8765)
RENTAL_RATE_LIMIT        requests/minute per key (default 30)

Copyright (c) 2026 Paris Pehlivanovic. All rights reserved.
PROPRIETARY — see LICENSE for terms.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def env(name, default=None, required=False):
    v = os.environ.get(name, default)
    if required and not v:
        raise SystemExit(f"{name} is required")
    return v


ADMIN_TOKEN = env("RENTAL_ADMIN_TOKEN", required=True)
PROVIDER_KEY = env("RENTAL_PROVIDER_KEY", required=True)
PROVIDER_BASE = env("RENTAL_PROVIDER_BASE_URL", "https://api.openai.com/v1").rstrip("/")
MODEL = env("RENTAL_MODEL", "gpt-4o-mini")
PROMPT_DIR = Path(env("RENTAL_PROMPT_DIR", str(HERE.parent / "assistant")))
DB_PATH = Path(env("RENTAL_DB", str(HERE / "rental.db")))
PORT = int(env("RENTAL_PORT", "8765"))
RATE_LIMIT = int(env("RENTAL_RATE_LIMIT", "30"))

TIERS = {
    "starter": 1_000,
    "pro": 10_000,
    "enterprise": 0,  # 0 = unlimited
}

SYSTEM_PROMPT = (PROMPT_DIR / "SYSTEM_PROMPT.md").read_text()
KNOWLEDGE = (PROMPT_DIR / "KNOWLEDGE.md").read_text()
GUIDE_SYSTEM = (SYSTEM_PROMPT + "\n\nKNOWLEDGE BASE:\n" + KNOWLEDGE)[:12000]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_db_lock = threading.Lock()

def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _db_lock, db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                key_prefix TEXT NOT NULL,
                tier TEXT NOT NULL DEFAULT 'starter',
                monthly_limit INTEGER NOT NULL DEFAULT 1000,
                revoked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usage (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id),
                ts TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_usage_customer_ts
                ON usage(customer_id, ts);
        """)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def month_start() -> str:
    return datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def create_customer(name: str, tier: str = "starter",
                    monthly_limit: int | None = None) -> dict:
    tier = tier.lower()
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; choose from {sorted(TIERS)}")
    limit = TIERS[tier] if monthly_limit is None else int(monthly_limit)
    raw = "civ_" + secrets.token_urlsafe(32)
    with _db_lock, db() as conn:
        cur = conn.execute(
            "INSERT INTO customers (name, key_hash, key_prefix, tier,"
            " monthly_limit, created_at) VALUES (?,?,?,?,?,?)",
            (name, hash_key(raw), raw[:10] + "…", tier, limit,
             datetime.now(timezone.utc).isoformat()))
        cid = cur.lastrowid
    return {"id": cid, "name": name, "tier": tier,
            "monthly_limit": limit, "api_key": raw}


def find_customer(raw: str) -> sqlite3.Row | None:
    with _db_lock, db() as conn:
        row = conn.execute(
            "SELECT * FROM customers WHERE key_hash = ? AND revoked = 0",
            (hash_key(raw),)).fetchone()
    return row


def log_usage(customer_id: int, model: str, usage: dict):
    with _db_lock, db() as conn:
        conn.execute(
            "INSERT INTO usage (customer_id, ts, model, prompt_tokens,"
            " completion_tokens, total_tokens) VALUES (?,?,?,?,?,?)",
            (customer_id, datetime.now(timezone.utc).isoformat(), model,
             int(usage.get("prompt_tokens", 0)),
             int(usage.get("completion_tokens", 0)),
             int(usage.get("total_tokens", 0))))


def usage_summary(customer_id: int) -> dict:
    with _db_lock, db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS messages,
                      COALESCE(SUM(total_tokens),0) AS tokens
               FROM usage WHERE customer_id = ? AND ts >= ?""",
            (customer_id, month_start())).fetchone()
    return {"messages": row["messages"], "tokens": row["tokens"]}


# ---------------------------------------------------------------------------
# Rate limiting (in-memory, per key hash)
# ---------------------------------------------------------------------------

_hits: dict[str, list[float]] = {}
_hits_lock = threading.Lock()


def rate_ok(key_hash: str) -> bool:
    now = time.time()
    with _hits_lock:
        lst = _hits.setdefault(key_hash, [])
        lst[:] = [t for t in lst if now - t < 60]
        if len(lst) >= RATE_LIMIT:
            return False
        lst.append(now)
        return True


# ---------------------------------------------------------------------------
# Upstream provider
# ---------------------------------------------------------------------------

class ProviderError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def provider_chat(messages: list, model: str, **kwargs) -> dict:
    payload = {"model": model, "messages": messages}
    payload.update(kwargs)
    req = urllib.request.Request(
        f"{PROVIDER_BASE}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {PROVIDER_KEY}",
                 "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("error", {}).get(
                "message", "")
        except Exception:
            detail = ""
        raise ProviderError(e.code, detail or f"upstream HTTP {e.code}")
    except urllib.error.URLError as e:
        raise ProviderError(502, f"upstream unreachable: {e.reason}")


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "CIVRental/1.0"

    # -- helpers ---------------------------------------------------------
    def _send(self, status: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode())
        except Exception:
            return {}

    def _bearer(self) -> str:
        auth = self.headers.get("Authorization", "")
        m = re.match(r"(?i)^Bearer\s+(\S+)$", auth.strip())
        return m.group(1) if m else ""

    def _require_admin(self) -> bool:
        if not hmac.compare_digest(self._bearer(), ADMIN_TOKEN):
            self._send(401, {"error": "admin authentication required"})
            return False
        return True

    def _require_customer(self) -> sqlite3.Row | None:
        raw = self._bearer()
        if not raw:
            self._send(401, {"error": "customer API key required"})
            return None
        cust = find_customer(raw)
        if cust is None:
            self._send(401, {"error": "invalid or revoked API key"})
            return None
        if not rate_ok(cust["key_hash"]):
            self._send(429, {"error": "rate limit exceeded, slow down"})
            return None
        return cust

    def log_message(self, fmt, *args):  # quieter logs
        pass

    # -- routing ---------------------------------------------------------
    def do_GET(self):
        if self.path == "/healthz":
            return self._send(200, {"ok": True})
        if self.path == "/v1/usage":
            cust = self._require_customer()
            if cust is None:
                return
            used = usage_summary(cust["id"])
            limit = cust["monthly_limit"]
            return self._send(200, {
                "tier": cust["tier"],
                "messages_used": used["messages"],
                "messages_limit": limit if limit else "unlimited",
                "tokens_total": used["tokens"],
            })
        if self.path == "/admin/customers":
            if not self._require_admin():
                return
            with _db_lock, db() as conn:
                rows = conn.execute(
                    "SELECT id,name,key_prefix,tier,monthly_limit,revoked,"
                    "created_at FROM customers ORDER BY id").fetchall()
            return self._send(200, {"customers": [dict(r) for r in rows]})
        if self.path == "/admin/usage":
            if not self._require_admin():
                return
            with _db_lock, db() as conn:
                rows = conn.execute(
                    """SELECT c.id, c.name, c.tier, COUNT(u.id) AS messages,
                              COALESCE(SUM(u.total_tokens),0) AS tokens
                       FROM customers c LEFT JOIN usage u
                         ON u.customer_id = c.id AND u.ts >= ?
                       GROUP BY c.id ORDER BY c.id""",
                    (month_start(),)).fetchall()
            return self._send(200, {"usage": [dict(r) for r in rows]})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            return self._chat()
        if self.path == "/admin/customers":
            if not self._require_admin():
                return
            data = self._body()
            try:
                cust = create_customer(
                    data.get("name", "unnamed"),
                    data.get("tier", "starter"),
                    data.get("monthly_limit"))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            # api_key is shown ONCE — it is stored only as a hash.
            return self._send(201, cust)
        m = re.fullmatch(r"/admin/customers/(\d+)/revoke", self.path)
        if m:
            if not self._require_admin():
                return
            with _db_lock, db() as conn:
                cur = conn.execute(
                    "UPDATE customers SET revoked = 1 WHERE id = ?",
                    (int(m.group(1)),))
            if cur.rowcount:
                return self._send(200, {"revoked": int(m.group(1))})
            return self._send(404, {"error": "customer not found"})
        return self._send(404, {"error": "not found"})

    # -- chat proxy ------------------------------------------------------
    def _chat(self):
        cust = self._require_customer()
        if cust is None:
            return
        limit = cust["monthly_limit"]
        if limit and usage_summary(cust["id"])["messages"] >= limit:
            return self._send(402, {
                "error": "monthly message limit reached",
                "tier": cust["tier"],
                "contact": "Pehlivanovicparis@gmail.com",
            })
        data = self._body()
        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            return self._send(400, {"error": "messages[] is required"})
        model = data.get("model", MODEL)
        # Inject the CIV-GUIDE identity ahead of the customer's messages.
        upstream_messages = ([{"role": "system", "content": GUIDE_SYSTEM}]
                             + messages)
        passthrough = {k: v for k, v in data.items()
                       if k in ("temperature", "max_tokens", "top_p",
                                "frequency_penalty", "presence_penalty")}
        try:
            result = provider_chat(upstream_messages, model, **passthrough)
        except ProviderError as e:
            return self._send(502, {"error": f"provider error: {e}"})
        log_usage(cust["id"], model, result.get("usage", {}))
        return self._send(200, result)


def main():
    init_db()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"CIV rental backend on 127.0.0.1:{PORT} "
          f"(model={MODEL}, db={DB_PATH})")
    print("Put it behind a reverse proxy (nginx/Caddy) with TLS for production.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
