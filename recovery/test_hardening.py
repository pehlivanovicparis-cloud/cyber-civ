"""Tests for the recovery API hardening (2026-11-02).

Covers: rate limiting (mutate/read buckets, 429 + Retry-After),
failed-auth blocking (brute-force defense), request-body cap (413),
signal length cap (400), and graceful /sense failure (clean 500,
never a dropped connection or a stack trace).

Run:  python3 -m unittest discover -s recovery -p "test_*.py" -t .
"""

import base64
import json
import threading
import unittest
import urllib.error
import urllib.request

from recovery.ratelimit import RateLimiter
from recovery.service import MAX_BODY_BYTES, MAX_SIGNAL_LEN, serve
from recovery import signal as signal_mod


def _auth_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


class _Client:
    """Tiny HTTP client for the recovery API."""

    def __init__(self, base: str, auth: str = None):
        self.base = base
        self.auth = auth

    def _req(self, method: str, path: str, body=None, raw_body=None,
             headers=None):
        hdrs = dict(headers or {})
        if self.auth:
            hdrs["Authorization"] = self.auth
        data = raw_body
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path, data=data, headers=hdrs, method=method
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def get(self, path: str):
        status, headers, raw = self._req("GET", path)
        return status, headers, json.loads(raw.decode() or "{}")

    def post(self, path: str, body: dict):
        status, headers, raw = self._req("POST", path, body=body)
        return status, headers, json.loads(raw.decode() or "{}")


class _ServerMixin:
    """Start the real server on an ephemeral localhost port."""

    def _start(self, auth=False, rate_limiter=None):
        server = serve("127.0.0.1", 0, auth=auth, state_path=False,
                       rate_limiter=rate_limiter)
        thread = threading.Thread(target=server.serve_forever,
                                  daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        host, port = server.server_address
        return f"http://{host}:{port}"


class TestRateLimitMutate(unittest.TestCase, _ServerMixin):
    def test_mutate_limit_blocks_after_n(self):
        # Small limit so the test is fast and deterministic.
        limiter = RateLimiter(mutate_limit=3, read_limit=100)
        base = self._start(auth=False, rate_limiter=limiter)
        client = _Client(base)
        # Three mutating requests are allowed.
        for _ in range(3):
            status, _, _ = client.post(
                "/cycles/run", {"force_null_target": False})
            self.assertEqual(status, 200)
        # The fourth is rate-limited.
        status, headers, body = client.post(
            "/cycles/run", {"force_null_target": False})
        self.assertEqual(status, 429)
        self.assertIn("Retry-After", headers)
        self.assertEqual(body.get("error"), "too many requests")

    def test_read_limit_is_higher_than_mutate(self):
        limiter = RateLimiter(mutate_limit=2, read_limit=100)
        base = self._start(auth=False, rate_limiter=limiter)
        client = _Client(base)
        # Exhaust the mutate bucket.
        for _ in range(2):
            client.post("/cycles/run", {})
        status, _, _ = client.post("/cycles/run", {})
        self.assertEqual(status, 429)
        # Read endpoints still work (separate bucket).
        status, _, body = client.get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body.get("status"), "ok")


class TestAuthBruteForce(unittest.TestCase, _ServerMixin):
    def test_repeated_failed_auth_blocks_ip(self):
        limiter = RateLimiter(auth_fail_limit=3, auth_block_seconds=60)
        base = self._start(auth=("user", "correct"),
                           rate_limiter=limiter)
        wrong = _Client(base, auth=_auth_header("user", "wrong"))
        # Three failed attempts -> 401 each.
        for _ in range(3):
            status, _, _ = wrong.get("/health")
            self.assertEqual(status, 401)
        # Fourth attempt -> blocked (429), not 401.
        status, headers, body = wrong.get("/health")
        self.assertEqual(status, 429)
        self.assertIn("Retry-After", headers)
        # Correct credentials are also blocked while the IP is blocked.
        right = _Client(base, auth=_auth_header("user", "correct"))
        status, _, _ = right.get("/health")
        self.assertEqual(status, 429)

    def test_correct_auth_still_works(self):
        base = self._start(auth=("user", "correct"))
        client = _Client(base, auth=_auth_header("user", "correct"))
        status, _, body = client.get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body.get("status"), "ok")

    def test_wrong_auth_is_401_not_500(self):
        base = self._start(auth=("user", "correct"))
        client = _Client(base, auth=_auth_header("user", "wrong"))
        status, _, body = client.get("/health")
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "authentication required")


class TestBodyAndSignalCaps(unittest.TestCase, _ServerMixin):
    def test_body_too_large_returns_413(self):
        base = self._start(auth=False)
        # A body just over the cap.
        big = "x" * (MAX_BODY_BYTES + 1)
        status, _, body = _Client(base)._req(
            "POST", "/accounts", raw_body=big.encode(),
            headers={"Content-Type": "application/json"})
        self.assertEqual(status, 413)
        self.assertIn("too large", body.decode())

    def test_signal_too_long_returns_400(self):
        base = self._start(auth=False)
        long_signal = "0x" + "a" * (MAX_SIGNAL_LEN + 1)
        status, _, body = _Client(base).post(
            "/sense", {"signal": long_signal})
        self.assertEqual(status, 400)
        self.assertIn("too long", body.get("error", ""))

    def test_signal_at_max_length_is_accepted(self):
        # A 66-char tx hash is well under the cap and is processed
        # (it will fail to resolve without network, but returns 200
        # with an errors list, not a 400).
        base = self._start(auth=False)
        status, _, body = _Client(base).post(
            "/sense", {"signal": "0x" + "a" * 64})
        self.assertEqual(status, 200)
        self.assertEqual(body.get("signal_type"), "crypto_tx")


class TestSenseFailureGraceful(unittest.TestCase, _ServerMixin):
    def test_sense_internal_error_returns_clean_500(self):
        base = self._start(auth=False)
        original = signal_mod.SignalMonitor.sense

        def _boom(self, signal):
            raise RuntimeError("simulated parser crash")

        signal_mod.SignalMonitor.sense = _boom
        try:
            status, _, body = _Client(base).post(
                "/sense", {"signal": "0x" + "b" * 64})
        finally:
            signal_mod.SignalMonitor.sense = original
        self.assertEqual(status, 500)
        self.assertEqual(body.get("error"), "sensing failed")
        # No stack trace or exception text leaks to the client.
        self.assertNotIn("RuntimeError", json.dumps(body))
        self.assertNotIn("simulated", json.dumps(body))


class TestRateLimiterUnit(unittest.TestCase):
    def test_fixed_window_counts_and_resets(self):
        now = [1000.0]
        limiter = RateLimiter(mutate_limit=2, read_limit=2,
                              window_seconds=60.0,
                              clock=lambda: now[0])
        self.assertEqual(limiter.check("1.2.3.4", "mutate"), (True, 0.0))
        self.assertEqual(limiter.check("1.2.3.4", "mutate"), (True, 0.0))
        allowed, retry = limiter.check("1.2.3.4", "mutate")
        self.assertFalse(allowed)
        self.assertGreater(retry, 0.0)
        # A different IP is unaffected.
        self.assertEqual(limiter.check("5.6.7.8", "mutate"), (True, 0.0))
        # After the window passes, the bucket resets.
        now[0] += 61.0
        self.assertEqual(limiter.check("1.2.3.4", "mutate"), (True, 0.0))

    def test_auth_block_expires(self):
        now = [1000.0]
        limiter = RateLimiter(auth_fail_limit=2, auth_fail_window=60.0,
                              auth_block_seconds=300.0,
                              clock=lambda: now[0])
        limiter.record_auth_failure("1.2.3.4")
        limiter.record_auth_failure("1.2.3.4")
        self.assertTrue(limiter.is_auth_blocked("1.2.3.4"))
        now[0] += 301.0
        self.assertFalse(limiter.is_auth_blocked("1.2.3.4"))

    def test_memory_bound_evicts_stale_ips(self):
        # With a tiny cap, the limiter must evict the
        # least-recently-seen IPs instead of growing without bound.
        now = [1000.0]
        limiter = RateLimiter(mutate_limit=100, read_limit=100,
                              clock=lambda: now[0])
        import recovery.ratelimit as rl
        original_cap = rl.MAX_TRACKED_IPS
        rl.MAX_TRACKED_IPS = 3
        try:
            for i in range(5):
                limiter.check(f"10.0.0.{i}", "mutate")
                now[0] += 1.0
            tracked = len(limiter._last_seen)
            self.assertLessEqual(tracked, 3)
            # The most recently seen IPs survive; the oldest are gone.
            self.assertNotIn("10.0.0.0", limiter._last_seen)
            self.assertNotIn("10.0.0.1", limiter._last_seen)
            self.assertIn("10.0.0.4", limiter._last_seen)
        finally:
            rl.MAX_TRACKED_IPS = original_cap

    def test_empty_buckets_are_dropped(self):
        # An IP whose window expires must not keep a key forever.
        now = [1000.0]
        limiter = RateLimiter(mutate_limit=100, read_limit=100,
                              window_seconds=60.0,
                              clock=lambda: now[0])
        limiter.check("1.2.3.4", "mutate")
        self.assertIn(("1.2.3.4", "mutate"), limiter._hits)
        # After the window passes, the next check prunes the old hit
        # and the key is recreated fresh (one entry, not two).
        now[0] += 61.0
        limiter.check("1.2.3.4", "mutate")
        self.assertEqual(len(limiter._hits[("1.2.3.4", "mutate")]), 1)


class TestKeepAliveDaemon(unittest.TestCase):
    """The background keep-alive daemon pings local /health on a
    schedule to keep the worker process warm. Best-effort: a failed
    ping is swallowed, never surfaced."""

    def test_daemon_pings_health(self):
        import time as _time

        server = serve("127.0.0.1", 0, auth=False, state_path=False,
                       rate_limiter=False, keep_alive_interval=0.5)
        thread = threading.Thread(target=server.serve_forever,
                                  daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(server.stop_keep_alive)
        # Wait for at least two pings (0.5s interval, generous
        # margin for a loaded CI runner).
        deadline = _time.monotonic() + 5.0
        while server.keep_alive_pings < 2 and _time.monotonic() < deadline:
            _time.sleep(0.1)
        self.assertGreaterEqual(
            server.keep_alive_pings, 2,
            "keep-alive daemon did not ping /health",
        )

    def test_daemon_disabled_by_default(self):
        server = serve("127.0.0.1", 0, auth=False, state_path=False,
                       rate_limiter=False)
        self.addCleanup(server.server_close)
        # No keep-alive thread is started when the interval is None.
        self.assertEqual(server.keep_alive_pings, 0)
        names = [t.name for t in threading.enumerate()]
        self.assertNotIn("recovery-keep-alive", names)

    def test_daemon_survives_a_dead_port(self):
        # If the server is shut down, the daemon's pings fail
        # silently instead of raising or spinning.
        import time as _time

        server = serve("127.0.0.1", 0, auth=False, state_path=False,
                       rate_limiter=False, keep_alive_interval=0.5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.stop_keep_alive)
        # Do NOT start serve_forever: every ping fails (connection
        # refused). The daemon must not raise.
        _time.sleep(1.2)
        self.assertEqual(server.keep_alive_pings, 0)


if __name__ == "__main__":
    unittest.main()
