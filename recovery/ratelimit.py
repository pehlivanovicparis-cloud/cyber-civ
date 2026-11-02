"""Cyber CIV — Recovery subsystem, rate limiting.

In-memory, stdlib-only rate limiting for the recovery HTTP API.

Why: the API is exposed publicly via a tunnel behind Basic Auth.
Without rate limiting, Basic Auth is vulnerable to credential
stuffing / brute force (an attacker can guess passwords as fast as
the server answers), and the slow /sense endpoint (public RPC calls,
up to ~15s per endpoint) can be abused to tie up server threads.

Design (no new dependencies — this service is stdlib-only):
- Per-client-IP buckets, fixed windows, in-memory. No Redis.
- Two buckets: "mutate" (POST endpoints that change state) and
  "read" (GET endpoints). Mutating endpoints get a strict limit;
  read endpoints get a higher one (the dashboard polls them).
- Failed-auth tracking: repeated failed Basic Auth attempts from
  one IP trigger a temporary block. This is the brute-force defense.
- All state is in-memory and resets on restart. That is acceptable:
  the limits are per-minute, so a restart does not grant a long
  window, and the server is a small single-instance service.

Thread safety: a single lock guards all counters (the server is
ThreadingHTTPServer, so requests run on multiple threads).

Memory bound: the per-IP dicts never grow without bound. Keys whose
lists prune to empty are dropped, and the number of tracked IPs is
capped at MAX_TRACKED_IPS (10,000) with least-recently-seen eviction
above that. A scan from many IPs cannot exhaust server memory.

Client identity behind the tunnel: the public endpoint is a
localhost.run SSH tunnel, so every remote client reaches the API as
127.0.0.1 (the local SSH client is the TCP peer). Per-IP limiting
therefore treats all remote clients as one client, sharing a bucket
with the local auto-cycle. That is acceptable for this single-user
private demo (Paris + the auto-cycle together use far less than the
limits), but it is a real limitation: one abusive remote client
could consume the shared bucket. A proper fix needs a proxy under
our control that sets a trusted client-IP header (e.g. a Cloudflare
Tunnel with our own domain, or an nginx reverse proxy). We do NOT
trust X-Forwarded-For from localhost.run, because we do not control
that proxy and the header is spoofable.

Order of checks: the rate limit is enforced BEFORE the request body
is read (do_POST checks the limit, then _json_body reads the body),
so an over-limit request never reaches the body cap — the body cap
is a second line of defense, not the only one.

Limits (chosen 2026-11-02, recorded here so a change is visible):
- MUTATE_LIMIT = 10 requests / 60s per IP (POST /accounts,
  /incidents, /transfers, /cycles/run). The auto-cycle calls
  POST /cycles/run once per minute, so 10/min leaves headroom for
  manual use while blocking automated guessing.
- READ_LIMIT = 60 requests / 60s per IP (GET endpoints). The
  dashboard polls /summary + /health every 10s (~12/min), so 60/min
  is generous.
- AUTH_FAIL_LIMIT = 5 failed auth attempts / 60s per IP, then the
  IP is blocked from auth for AUTH_BLOCK_SECONDS (300s). Five
  guesses a minute makes brute force infeasible for any real
  password, and a 5-minute block stops a sustained attack.

Responses: when limited, the service returns HTTP 429 with a
Retry-After header (seconds until the window resets).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

# Requests allowed per 60-second window, per client IP.
MUTATE_LIMIT = 10
READ_LIMIT = 60
WINDOW_SECONDS = 60.0

# Failed Basic Auth attempts allowed per 60s per IP before blocking.
AUTH_FAIL_LIMIT = 5
AUTH_FAIL_WINDOW_SECONDS = 60.0
# How long a blocked IP stays blocked (seconds).
AUTH_BLOCK_SECONDS = 300.0

# Maximum distinct client IPs tracked. A per-IP dict that never evicts
# entries grows without bound under a scan from many IPs, so the
# limiter caps the number of tracked IPs and evicts the
# least-recently-seen ones when the cap is hit. 10,000 IPs is far
# above any legitimate use of this single-user service.
MAX_TRACKED_IPS = 10000


class RateLimiter:
    """Per-IP fixed-window rate limiter + failed-auth blocking.

    All methods are thread-safe.
    """

    def __init__(
        self,
        mutate_limit: int = MUTATE_LIMIT,
        read_limit: int = READ_LIMIT,
        window_seconds: float = WINDOW_SECONDS,
        auth_fail_limit: int = AUTH_FAIL_LIMIT,
        auth_fail_window: float = AUTH_FAIL_WINDOW_SECONDS,
        auth_block_seconds: float = AUTH_BLOCK_SECONDS,
        clock=None,
    ) -> None:
        self.mutate_limit = mutate_limit
        self.read_limit = read_limit
        self.window_seconds = window_seconds
        self.auth_fail_limit = auth_fail_limit
        self.auth_fail_window = auth_fail_window
        self.auth_block_seconds = auth_block_seconds
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        # (ip, bucket) -> list of request timestamps (monotonic seconds)
        self._hits: dict = defaultdict(list)
        # ip -> list of failed-auth timestamps
        self._auth_fails: dict = defaultdict(list)
        # ip -> timestamp until which auth is blocked
        self._auth_blocked_until: dict = {}
        # ip -> last-seen timestamp (for eviction when the cap is hit)
        self._last_seen: dict = {}

    def _now(self) -> float:
        return self._clock()

    def _prune(self, entries: list, now: float, window: float) -> list:
        cutoff = now - window
        return [t for t in entries if t > cutoff]

    def _touch(self, ip: str, now: float) -> None:
        """Record that ip was seen at now (for eviction ordering)."""
        self._last_seen[ip] = now

    def _evict_if_needed(self, now: float) -> None:
        """Bound memory: if more than MAX_TRACKED_IPS distinct IPs are
        tracked, evict the least-recently-seen ones until under the
        cap. Also drops any keys whose lists are empty after pruning
        (an IP that stops making requests must not keep a key
        forever). Only runs when the cap is exceeded, so the common
        case (a handful of IPs) costs nothing."""
        # Drop keys whose lists are empty (pruned to nothing).
        for key in list(self._hits.keys()):
            if not self._hits[key]:
                del self._hits[key]
        for ip in list(self._auth_fails.keys()):
            if not self._auth_fails[ip]:
                del self._auth_fails[ip]
        tracked = set(self._last_seen.keys())
        if len(tracked) <= MAX_TRACKED_IPS:
            return
        # Evict least-recently-seen IPs until under the cap.
        ordered = sorted(tracked, key=lambda ip: self._last_seen[ip])
        for ip in ordered[: len(tracked) - MAX_TRACKED_IPS]:
            for bucket in ("mutate", "read"):
                self._hits.pop((ip, bucket), None)
            self._auth_fails.pop(ip, None)
            self._auth_blocked_until.pop(ip, None)
            self._last_seen.pop(ip, None)

    def check(self, ip: str, bucket: str) -> tuple:
        """Check whether a request from ip in bucket is allowed.

        bucket: "mutate" or "read". Returns (allowed, retry_after).
        allowed is True if the request may proceed. retry_after is
        the seconds to wait before retrying (0 when allowed).
        """
        limit = self.mutate_limit if bucket == "mutate" else self.read_limit
        now = self._now()
        key = (ip, bucket)
        with self._lock:
            self._touch(ip, now)
            hits = self._prune(self._hits[key], now, self.window_seconds)
            if len(hits) >= limit:
                retry_after = max(
                    0.0, self.window_seconds - (now - hits[0])
                )
                if hits:
                    self._hits[key] = hits
                else:
                    self._hits.pop(key, None)
                self._evict_if_needed(now)
                return False, retry_after
            hits.append(now)
            self._hits[key] = hits
            self._evict_if_needed(now)
            return True, 0.0

    def record_auth_failure(self, ip: str) -> None:
        """Record a failed Basic Auth attempt from ip."""
        now = self._now()
        with self._lock:
            self._touch(ip, now)
            fails = self._prune(
                self._auth_fails[ip], now, self.auth_fail_window
            )
            fails.append(now)
            self._auth_fails[ip] = fails
            if len(fails) >= self.auth_fail_limit:
                self._auth_blocked_until[ip] = now + self.auth_block_seconds
            self._evict_if_needed(now)

    def is_auth_blocked(self, ip: str) -> bool:
        """True if ip is currently blocked from authenticating."""
        now = self._now()
        with self._lock:
            until = self._auth_blocked_until.get(ip, 0.0)
            if now >= until:
                # Block expired (or never set). Clean up.
                self._auth_blocked_until.pop(ip, None)
                return False
            return True

    def auth_block_remaining(self, ip: str) -> float:
        """Seconds remaining on ip's auth block (0 if not blocked)."""
        now = self._now()
        with self._lock:
            until = self._auth_blocked_until.get(ip, 0.0)
            return max(0.0, until - now)
