"""Cyber CIV — Recovery subsystem, auto-cycle loop.

Runs a recovery cycle against the live API every N seconds, so the
RecoveryAgent (Aegis) keeps monitoring and recovering without anyone
having to trigger a cycle by hand. New incidents get picked up and
recovered automatically; when nothing is open the cycle is a no-op.

Credentials are read from recovery/.auth (JSON {"user", "pass"}).
State lives in the API server, persisted to recovery/state.json
(loaded on startup, saved after every mutation); this loop only
triggers cycles, it holds no state itself.

Usage:
  python3 recovery/auto_cycle.py [--url URL] [--interval SECONDS]
  Defaults: http://127.0.0.1:8000, 60 seconds.

Stop: pkill -f "^python3 recovery/auto_cycle.py"
Log:  recovery/logs/auto_cycle.log
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


def _creds() -> tuple[str, str]:
    auth_file = Path(__file__).resolve().parent / ".auth"
    data = json.loads(auth_file.read_text())
    return str(data["user"]), str(data["pass"])


def _run_cycle(base: str, user: str, password: str) -> dict:
    req = urllib.request.Request(
        base.rstrip("/") + "/cycles/run",
        data=json.dumps({}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    import base64
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-cycle the recovery API.")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--interval", type=float, default=60.0)
    args = parser.parse_args()
    user, password = _creds()
    print(f"Auto-cycle started: {args.url} every {args.interval}s",
          flush=True)
    while True:
        try:
            result = _run_cycle(args.url, user, password)
            print(
                f"cycle {result['cycle']}: target={result['agent_target']} "
                f"guard={result['guard']} "
                f"agent={result['agent_recovered']} "
                f"fallback={result['fallback_recovered']} "
                f"open={result['open_losses']} "
                f"pool={result['pool_balance']}",
                flush=True,
            )
        except urllib.error.HTTPError as exc:
            print(f"cycle failed: HTTP {exc.code}", flush=True)
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            print(f"cycle failed: {exc}", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
