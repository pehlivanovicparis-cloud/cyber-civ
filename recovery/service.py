"""Cyber CIV — Recovery subsystem, HTTP API service.

A small JSON API over the recovery engine, using only the Python
standard library (no dependencies). This is the service layer that
turns the engine into deployable software: a private user can run
it locally to protect their own accounts, and a bank can run it
behind its own network to protect itself and its clients.

Endpoints:
  GET  /health        {"status": "ok", "cycle": N, "audit_chain_valid": bool}
  GET  /accounts      list of accounts
  POST /accounts      create an account
                       {"account_id", "account_type", "owner", "balance", "currency"}
  POST /incidents     report a cyber incident
                       {"victim_id", "amount_stolen", "amount_frozen", "kind"}
  POST /cycles/run    run one recovery cycle
                       {"force_null_target": false}
  POST /transfers     send funds from an account to a destination address
                       {"from_account_id", "to_address", "amount"}
  GET  /transfers     list outbound transfers
  GET  /summary       full state (accounts, pool, recovery wallet, transfers, audit status)
  GET  /audit         the hash-chained audit log + chain validity
  GET  /              API index (this list)
  GET  /dashboard     web dashboard (accounts, incidents, transfers, audit, signal sensing)
  POST /sense         sense money from a mere signal
                       {"signal": "0x... tx hash, wallet address, bank:<routing>:<account>, or ref:<reference>"}
  POST /ingest/mt940  parse an MT940 statement snippet (read-only)
                       {"text": "<raw MT940>"}
  POST /ingest/xpub   validate an xpub, derive addresses, resolve balances
                       {"xpub": "<xpub/ypub/zpub>", "gap_limit": 20, "base_url": "<indexer>"}

Ingest endpoints are read-only sensing (like /sense): they do not
change engine state and do not persist. Results are returned
in-memory in the response (and shown on the dashboard). The
ingest result is NOT written into the cycle persistence layer —
sensing a user's signal must not mutate the engine's accounts,
incidents, or pool.

Safety defaults:
  - Binds to 127.0.0.1 (localhost only) unless --host is given.
    Binding to 0.0.0.0 exposes the API on the network; do that only
    behind authentication and TLS (see README.md).
  - State is persisted to recovery/state.json (loaded on startup,
    saved after every mutation), so a restart resumes where it left
    off. The state file is git-ignored. A bank deployment should
    also back up the state file and the audit log — see README.md.

Authentication (Basic Auth):
  - If credentials are configured, EVERY request must carry a valid
    `Authorization: Basic ...` header, or it gets a 401. Browsers
    prompt for the username/password automatically.
  - Credentials come from, in order: the RECOVERY_USER /
    RECOVERY_PASS environment variables, then a `recovery/.auth`
    file (JSON: {"user": "...", "pass": "..."}), then no auth.
  - No credentials configured = no auth (local-only use). NEVER
    expose the API publicly without credentials configured.
  - The `.auth` file is 0600 and git-ignored; it holds the password.
  - Credential comparison is constant-time (hmac.compare_digest),
    so there is no timing side channel.

Hardening (2026-11-02, recovery/ratelimit.py):
  - Rate limiting, per client IP, in-memory (stdlib only, no new
    dependencies): POST (mutating) endpoints 10/min, GET endpoints
    60/min, over-limit returns 429 with Retry-After.
  - Failed Basic Auth attempts are tracked per IP: 5 failures in a
    minute blocks that IP from authenticating for 5 minutes (429).
    This is the brute-force defense for Basic Auth over a public
    tunnel.
  - Request bodies are capped at 64 KB (413 above that).
  - /sense signals are capped at 256 chars (400 above that), and
    sensing failures return a clean 500, never a dropped connection
    or a stack trace to the client.
  - The signal parser itself is strict by construction: tx hashes
    and addresses must match anchored regexes (^0x + exact hex
    length), bank signals must be digits with a valid ABA checksum.
    A malformed signal is rejected, never executed.

Usage:
  python3 -m recovery.cli serve            # localhost:8000
  python3 -m recovery.cli serve --port 8080
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Tuple

from .bip32 import BIP32DerivationError, derive_addresses
from .engine import Account, RecoveryEngine
from .mt940 import MT940ParseError, parse_mt940
from .ratelimit import RateLimiter
from .signal import SignalMonitor
from .state import DEFAULT_STATE_PATH, load_state, save_state
from .utxo import DEFAULT_BASE_URL, IndexerError, get_address_info
from .xpub import XpubValidationError, validate_xpub

# Keep-alive interval (seconds) for the background daemon that
# pings local /health to keep the worker process warm. 300s (5 min)
# is far below the read rate limit (60/min) and keeps the process
# pinned in memory. NOTE: a local loopback ping warms the API
# process only — it does NOT traverse the SSH tunnel, so it does
# not warm the tunnel's cold start. The tunnel cold start is
# handled by the dashboard's client-side retry (fetchWithRetry).
# The daemon is best-effort: any failure is swallowed silently.
DEFAULT_KEEP_ALIVE_INTERVAL = 300.0

# Maximum request body size (bytes). The largest legitimate body is a
# small JSON object (an account, an incident, a transfer). 64 KB is
# generous; anything larger is rejected with 413. This stops a client
# from exhausting server memory with a huge Content-Length.
MAX_BODY_BYTES = 65536

# Maximum signal string length (characters) for POST /sense. A tx hash
# is 66 chars, an address 42, a bank signal well under 100. 256 is
# generous; anything longer is rejected with 400.
MAX_SIGNAL_LEN = 256


def _load_auth() -> Optional[Tuple[str, str]]:
    """Return (user, password) if credentials are configured, else None.

    Order: RECOVERY_USER/RECOVERY_PASS env vars, then recovery/.auth
    (JSON {"user": ..., "pass": ...}). No credentials = no auth.
    """
    user = os.environ.get("RECOVERY_USER")
    password = os.environ.get("RECOVERY_PASS")
    if user and password:
        return user, password
    auth_file = Path(__file__).resolve().parent / ".auth"
    try:
        data = json.loads(auth_file.read_text())
        user = str(data["user"])
        password = str(data["pass"])
        if user and password:
            return user, password
    except (OSError, ValueError, KeyError):
        pass
    return None


class BodyTooLargeError(ValueError):
    """Request body exceeds MAX_BODY_BYTES. Mapped to HTTP 413."""


def _json_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    if length > MAX_BODY_BYTES:
        raise BodyTooLargeError(
            f"request body too large: {length} bytes "
            f"(max {MAX_BODY_BYTES})"
        )
    raw = handler.rfile.read(length)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("request body is not valid JSON")
    if not isinstance(data, dict):
        raise ValueError("request body must be a JSON object")
    return data


class RecoveryHTTPServer(ThreadingHTTPServer):
    """HTTP server holding one RecoveryEngine instance.

    auth: None = auto-load from env vars / recovery/.auth;
          False = no auth (local-only / tests);
          (user, password) tuple = enforce these credentials.

    state_path: where the engine state is persisted. On startup the
    server loads the state file if it exists (so a restart resumes
    where it left off); after every mutating request it saves the
    state. None = DEFAULT_STATE_PATH (recovery/state.json).
    False = no persistence (tests / throwaway runs).
    """

    def __init__(self, address, engine: Optional[RecoveryEngine] = None,
                 auth=None, state_path=None,
                 rate_limiter: Optional[RateLimiter] = None,
                 keep_alive_interval: Optional[float] = None):
        # Persistence rule: an explicitly supplied engine belongs to
        # its caller (e.g. tests), so it is NOT persisted unless the
        # caller passes a state_path. When no engine is supplied, the
        # server loads state.json on startup and persists to it.
        if state_path is False:
            self.state_path = None
        elif state_path is not None:
            self.state_path = state_path
            if engine is None:
                loaded = load_state(state_path)
                if loaded is not None:
                    engine = loaded
        elif engine is None:
            self.state_path = DEFAULT_STATE_PATH
            loaded = load_state(DEFAULT_STATE_PATH)
            if loaded is not None:
                engine = loaded
        else:
            self.state_path = None
        self.engine = engine or RecoveryEngine()
        if auth is False:
            self.auth = None
        elif auth is None:
            self.auth = _load_auth()
        else:
            self.auth = auth
        # Rate limiting (in-memory, per client IP). None = a fresh
        # default limiter. Pass rate_limiter=False to disable it
        # (tests that need unlimited requests).
        if rate_limiter is False:
            self.rate_limiter = None
        else:
            self.rate_limiter = rate_limiter or RateLimiter()
        # Keep-alive pings (best-effort). Counts successful local
        # /health pings; tests assert on this counter.
        self.keep_alive_pings = 0
        self._keep_alive_stop = threading.Event()
        super().__init__(address, RecoveryRequestHandler)
        self.daemon_threads = True
        if keep_alive_interval:
            self._start_keep_alive(keep_alive_interval)

    def _start_keep_alive(self, interval: float) -> None:
        """Start the background keep-alive daemon.

        A daemon thread that wakes every `interval` seconds and
        GETs local /health (loopback, with auth when configured)
        to keep the worker process warm in memory. Best-effort:
        any failure is swallowed. The thread is a daemon, so it
        dies when the process exits.
        """
        def _loop():
            while not self._keep_alive_stop.is_set():
                # Sleep first so the server is accepting connections
                # before the first ping.
                if self._keep_alive_stop.wait(interval):
                    break
                try:
                    port = self.server_address[1]
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/health"
                    )
                    if self.auth is not None:
                        user, password = self.auth
                        token = base64.b64encode(
                            f"{user}:{password}".encode()
                        ).decode()
                        req.add_header(
                            "Authorization", f"Basic {token}"
                        )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        if resp.status == 200:
                            self.keep_alive_pings += 1
                except Exception:
                    # Best-effort only. A failed ping is not an
                    # error worth surfacing.
                    pass

        thread = threading.Thread(
            target=_loop, name="recovery-keep-alive", daemon=True
        )
        thread.start()

    def stop_keep_alive(self) -> None:
        """Stop the keep-alive daemon (tests / shutdown)."""
        self._keep_alive_stop.set()

    def persist(self) -> None:
        """Save the engine state to disk (no-op when persistence is
        off). Called after every mutating request."""
        if self.state_path is not None:
            save_state(self.engine, self.state_path)


class RecoveryRequestHandler(BaseHTTPRequestHandler):
    server: RecoveryHTTPServer  # type: ignore[assignment]

    # -- helpers ----------------------------------------------------------
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send(status, {"error": message})

    def _check_auth(self) -> bool:
        """Validate Basic Auth. Returns True if the request is allowed.

        If no credentials are configured on the server, everything is
        allowed (local-only mode). Otherwise the request must carry a
        valid `Authorization: Basic <base64(user:pass)>` header.
        Comparison is constant-time (hmac.compare_digest).
        """
        creds = self.server.auth
        if creds is None:
            return True
        header = self.headers.get("Authorization") or ""
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(
                header[len("Basic "):].strip()
            ).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return False
        user, sep, password = decoded.partition(":")
        if not sep:
            return False
        expected_user, expected_pass = creds
        return (
            hmac.compare_digest(user, expected_user)
            and hmac.compare_digest(password, expected_pass)
        )

    def _client_ip(self) -> str:
        """Client IP for rate limiting. The tunnel terminates TLS and
        forwards the real client address as the TCP peer, so
        client_address is the right key. (If a proxy is ever placed
        in front, use X-Forwarded-For only from a trusted proxy.)"""
        return self.client_address[0]

    def _rate_limited(self, retry_after: float) -> None:
        """Send a 429 Too Many Requests with a Retry-After header."""
        body = json.dumps(
            {"error": "too many requests",
             "retry_after_seconds": round(retry_after, 1)}
        ).encode()
        self.send_response(429)
        self.send_header("Content-Type", "application/json")
        self.send_header("Retry-After", str(int(retry_after) + 1))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_rate_limit(self, bucket: str) -> bool:
        """Enforce the per-IP rate limit for bucket ('read'/'mutate').

        Returns True if the request may proceed. Sends a 429 and
        returns False when the IP is over the limit. A disabled
        limiter (None) allows everything.
        """
        limiter = self.server.rate_limiter
        if limiter is None:
            return True
        allowed, retry_after = limiter.check(self._client_ip(), bucket)
        if allowed:
            return True
        self._rate_limited(retry_after)
        return False

    def _require_auth(self) -> bool:
        """Send a 401 and return False if the request is not authorized.

        Failed attempts are recorded per IP; after
        AUTH_FAIL_LIMIT failures in a minute the IP is blocked from
        authenticating for AUTH_BLOCK_SECONDS (429 instead of 401).
        This is the brute-force defense for Basic Auth over a public
        tunnel.
        """
        creds = self.server.auth
        if creds is None:
            return True
        limiter = self.server.rate_limiter
        ip = self._client_ip()
        if limiter is not None and limiter.is_auth_blocked(ip):
            self._rate_limited(limiter.auth_block_remaining(ip))
            return False
        if self._check_auth():
            return True
        if limiter is not None:
            limiter.record_auth_failure(ip)
        body = json.dumps({"error": "authentication required"}).encode()
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="cyber-civ-recovery"')
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    def log_message(self, fmt, *args):  # keep stdout clean
        pass

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        if not self._require_auth():
            return
        if not self._check_rate_limit("read"):
            return
        engine = self.server.engine
        if self.path == "/":
            self._send(200, {
                "service": "cyber-civ-recovery",
                "endpoints": {
                    "GET /health": "liveness + audit chain status",
                    "GET /accounts": "list accounts",
                    "POST /accounts": "open an account",
                    "POST /incidents": "report a cyber incident",
                    "POST /cycles/run": "run one recovery cycle",
                    "POST /transfers": "send funds to a destination address",
                    "GET /transfers": "list outbound transfers",
                    "GET /summary": "full state",
                    "GET /audit": "hash-chained audit log",
                    "GET /dashboard": "web dashboard",
                    "POST /sense": "sense money from a mere signal",
                    "POST /ingest/mt940": "parse an MT940 statement snippet",
                    "POST /ingest/xpub": "validate an xpub, derive addresses, resolve balances",
                },
            })
        elif self.path == "/dashboard":
            dashboard = Path(__file__).resolve().parent / "dashboard.html"
            try:
                body = dashboard.read_bytes()
            except OSError:
                self._error(404, "dashboard not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            self._send(200, {
                "status": "ok",
                "cycle": engine.cycle,
                "audit_chain_valid": engine.log.verify(),
            })
        elif self.path == "/accounts":
            self._send(200, {"accounts": engine.summary()["accounts"]})
        elif self.path == "/transfers":
            self._send(200, {"transfers": engine.transfers})
        elif self.path == "/summary":
            self._send(200, engine.summary())
        elif self.path == "/audit":
            self._send(200, {
                "chain_valid": engine.log.verify(),
                "entries": engine.log.entries,
            })
        else:
            self._error(404, f"unknown path: {self.path}")
    def do_POST(self):
        if not self._require_auth():
            return
        if not self._check_rate_limit("mutate"):
            return
        engine = self.server.engine
        try:
            body = _json_body(self)
        except BodyTooLargeError as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(413)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        except ValueError as exc:
            self._error(400, str(exc))
            return

        if self.path == "/accounts":
            try:
                account = Account(
                    account_id=str(body["account_id"]),
                    account_type=str(body["account_type"]),
                    owner=str(body["owner"]),
                    balance=float(body["balance"]),
                    currency=str(body["currency"]),
                    address=(str(body["address"])
                             if body.get("address") else None),
                )
                engine.add_account(account)
            except (KeyError, TypeError, ValueError) as exc:
                self._error(400, str(exc))
                return
            self.server.persist()
            self._send(201, {"account_id": account.account_id,
                             "balance": account.balance,
                             "currency": account.currency})
        elif self.path == "/incidents":
            try:
                incident = engine.report_incident(
                    victim_id=str(body["victim_id"]),
                    amount_stolen=float(body["amount_stolen"]),
                    amount_frozen=float(body["amount_frozen"]),
                    kind=str(body.get("kind", "unauthorized_transfer")),
                )
            except (KeyError, TypeError, ValueError) as exc:
                self._error(400, str(exc))
                return
            self.server.persist()
            self._send(201, {
                "incident_id": incident.incident_id,
                "victim": incident.victim_id,
                "stolen": incident.amount_stolen,
                "frozen": incident.amount_frozen,
                "lost": incident.amount_lost,
                "pool_balance": engine.pool.balance,
            })
        elif self.path == "/cycles/run":
            force_null = bool(body.get("force_null_target", False))
            result = engine.run_cycle(force_null_target=force_null)
            self.server.persist()
            self._send(200, result)
        elif self.path == "/transfers":
            try:
                record = engine.transfer(
                    from_account_id=str(body["from_account_id"]),
                    to_address=str(body["to_address"]),
                    amount=float(body["amount"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                self._error(400, str(exc))
                return
            self.server.persist()
            self._send(201, record)
        elif self.path == "/sense":
            # Sense money from a mere signal. Read-only: it does not
            # change engine state, so it does not persist. Returns the
            # SenseResult as JSON. The signal may be a transaction
            # hash, a wallet address, a bank signal, or a reference.
            signal = body.get("signal")
            if not signal or not str(signal).strip():
                self._error(400, "signal is required")
                return
            signal = str(signal)
            if len(signal) > MAX_SIGNAL_LEN:
                self._error(
                    400,
                    f"signal too long: {len(signal)} chars "
                    f"(max {MAX_SIGNAL_LEN})",
                )
                return
            try:
                result = SignalMonitor().sense(signal)
            except Exception:
                # Sensing must fail gracefully: a malformed signal or
                # an unexpected RPC failure returns a clean 500, never
                # a dropped connection or a stack trace to the client.
                self._error(500, "sensing failed")
                return
            self._send(200, {
                "signal": result.signal,
                "signal_type": result.signal_type,
                "resolved": result.resolved,
                "addresses": [
                    {"address": a.address,
                     "balance_eth": a.balance_eth,
                     "transaction_count": a.transaction_count}
                    for a in result.addresses
                ],
                "signals": [
                    {"kind": s.kind, "severity": s.severity,
                     "message": s.message, "details": s.details}
                    for s in result.signals
                ],
                "errors": result.errors,
            })
        elif self.path == "/ingest/mt940":
            # Parse an MT940 statement snippet. Read-only: it does
            # not change engine state and does not persist. The
            # parser strips :25: (account ID) and :28C: (statement
            # number) by construction; the response contains only
            # balance / currency / velocity state, never an account
            # identifier. Malformed input returns a clean 400.
            text = body.get("text")
            if not text or not str(text).strip():
                self._error(400, "text is required")
                return
            try:
                result = parse_mt940(str(text))
            except MT940ParseError as exc:
                self._error(400, str(exc))
                return
            self._send(200, {
                "currency": result.currency,
                "opening_balance": result.opening_balance,
                "closing_balance": result.closing_balance,
                "statement_period_start":
                    result.statement_period_start.isoformat(),
                "transaction_count": result.transaction_count,
                "velocity_profile": [
                    {"entry_date": v.entry_date.isoformat(),
                     "amount": v.amount,
                     "is_credit": v.is_credit,
                     "transaction_type": v.transaction_type}
                    for v in result.velocity_profile
                ],
            })
        elif self.path == "/ingest/xpub":
            # Validate an xpub, derive the receiving addresses up to
            # the gap limit, and resolve each address's balance via
            # the public indexer. Read-only: it does not change
            # engine state and does not persist. The derivation is
            # fast (~0.25s for 20 addresses); the balance lookups
            # run in parallel (5 workers) so a 20-address scan
            # finishes in seconds, not tens of seconds. A per-address
            # indexer failure leaves that address's balance null and
            # is recorded in "errors" instead of failing the request.
            xpub_key = body.get("xpub")
            if not xpub_key or not str(xpub_key).strip():
                self._error(400, "xpub is required")
                return
            gap_limit = body.get("gap_limit", 20)
            base_url = body.get("base_url") or DEFAULT_BASE_URL
            try:
                gap_limit = int(gap_limit)
            except (TypeError, ValueError):
                self._error(400, "gap_limit must be an integer")
                return
            try:
                signal = validate_xpub(str(xpub_key), gap_limit=gap_limit)
            except XpubValidationError as exc:
                self._error(400, str(exc))
                return
            try:
                addresses = derive_addresses(
                    signal.xpub_key, gap_limit=signal.gap_limit
                )
            except BIP32DerivationError as exc:
                self._error(400, str(exc))
                return

            def _lookup(address: str):
                try:
                    info = get_address_info(address, base_url=str(base_url))
                    return {
                        "address": address,
                        "balance_sats": info.balance_sats,
                        "tx_count": info.tx_count,
                        "utxo_count": len(info.utxos),
                    }
                except IndexerError as exc:
                    return {
                        "address": address,
                        "balance_sats": None,
                        "tx_count": None,
                        "utxo_count": None,
                        "error": str(exc),
                    }

            with ThreadPoolExecutor(max_workers=5) as pool:
                resolved = list(pool.map(_lookup, addresses))
            errors = [r["error"] for r in resolved if "error" in r]
            total_balance = sum(
                r["balance_sats"] for r in resolved
                if r["balance_sats"] is not None
            )
            total_txs = sum(
                r["tx_count"] for r in resolved
                if r["tx_count"] is not None
            )
            self._send(200, {
                "xpub": signal.xpub_key,
                "derivation_type": signal.derivation_type,
                "gap_limit": signal.gap_limit,
                "addresses": resolved,
                "total_balance_sats": total_balance,
                "total_tx_count": total_txs,
                "addresses_with_activity": sum(
                    1 for r in resolved
                    if r["tx_count"] is not None and r["tx_count"] > 0
                ),
                "errors": errors,
            })
        else:
            self._error(404, f"unknown path: {self.path}")


def serve(host: str = "127.0.0.1", port: int = 8000,
          engine: Optional[RecoveryEngine] = None,
          auth=None, state_path=None,
          rate_limiter=None,
          keep_alive_interval: Optional[float] = None) -> RecoveryHTTPServer:
    """Build (but do not start) the recovery API server.

    auth: None = auto-load (env vars / recovery/.auth);
          False = no auth; (user, password) = enforce these.
    state_path: persistence file (default recovery/state.json);
          False = no persistence.
    rate_limiter: None = a fresh default limiter; False = no rate
          limiting (tests that need unlimited requests).
    keep_alive_interval: seconds between background /health pings
          (None = disabled). The real service enables this.
    """
    return RecoveryHTTPServer((host, port), engine=engine, auth=auth,
                              state_path=state_path,
                              rate_limiter=rate_limiter,
                              keep_alive_interval=keep_alive_interval)


def run_server(host: str = "127.0.0.1", port: int = 8000,
               engine: Optional[RecoveryEngine] = None,
               auth=None, state_path=None,
               rate_limiter=None,
               keep_alive_interval: float = DEFAULT_KEEP_ALIVE_INTERVAL) -> None:
    server = serve(host=host, port=port, engine=engine, auth=auth,
                   state_path=state_path, rate_limiter=rate_limiter,
                   keep_alive_interval=keep_alive_interval)
    print(f"Cyber CIV recovery API listening on http://{host}:{port}")
    print("Auth: " + ("ON (Basic Auth required)"
                      if server.auth else "OFF (no credentials configured)"))
    print("State: " + (f"persisted to {server.state_path}"
                       if server.state_path else "in-memory only"))
    print("Endpoints: /health /accounts /incidents /cycles/run /transfers /summary /audit")
    print(f"Keep-alive: every {keep_alive_interval}s (local /health ping)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.stop_keep_alive()
        server.shutdown()
