"""End-to-end tests for the Cyber CIV Recovery HTTP API.

Starts the real server on an ephemeral localhost port and drives it
over HTTP with the standard library only.

Run:  python3 -m recovery.cli test
       python3 -m unittest discover -s recovery -p "test_*.py" -t .
"""

import base64
import json
import threading
import unittest
import urllib.error
import urllib.request

from recovery.engine import RecoveryEngine
from recovery.service import serve


class _Client:
    """Tiny HTTP client for the recovery API."""

    def __init__(self, base: str):
        self.base = base

    def get(self, path: str):
        try:
            with urllib.request.urlopen(self.base + path) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def post(self, path: str, body: dict):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())


class TestRecoveryAPI(unittest.TestCase):
    def setUp(self):
        # auth=False: these tests exercise the API itself, not auth.
        # Auth enforcement is covered by TestRecoveryAPIAuth below.
        self.server = serve("127.0.0.1", 0, engine=RecoveryEngine(),
                            auth=False)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.api = _Client(f"http://127.0.0.1:{self.port}")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_health_and_index(self):
        status, body = self.api.get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["audit_chain_valid"])
        status, body = self.api.get("/")
        self.assertEqual(status, 200)
        self.assertIn("endpoints", body)

    def test_unknown_path_is_404(self):
        status, body = self.api.get("/nope")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_full_flow_over_http(self):
        # Open a bank account.
        status, body = self.api.post("/accounts", {
            "account_id": "BANK-1", "account_type": "bank",
            "owner": "First National Bank", "balance": 10_000.00,
            "currency": "USD",
        })
        self.assertEqual(status, 201, body)
        # Duplicate account is rejected.
        status, body = self.api.post("/accounts", {
            "account_id": "BANK-1", "account_type": "bank",
            "owner": "First National Bank", "balance": 10_000.00,
            "currency": "USD",
        })
        self.assertEqual(status, 400, body)
        # Report a phishing incident: $1,200 stolen, $600 frozen.
        status, body = self.api.post("/incidents", {
            "victim_id": "BANK-1", "amount_stolen": 1200.00,
            "amount_frozen": 600.00, "kind": "phishing_transfer",
        })
        self.assertEqual(status, 201, body)
        self.assertEqual(body["incident_id"], 1)
        self.assertEqual(body["pool_balance"], 600.00)
        # Run a cycle: the agent recovers the $600.
        status, body = self.api.post("/cycles/run", {})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["agent_target"], "BANK-1")
        self.assertEqual(body["guard"], "accepted")
        self.assertEqual(body["agent_recovered"], 1)
        self.assertEqual(body["pool_balance"], 0.0)
        # Summary shows the recovery and a valid audit chain.
        status, body = self.api.get("/summary")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["accounts"]["BANK-1"]["balance"], 9_400.00)
        self.assertEqual(body["total_recovered"], 600.00)
        self.assertEqual(body["open_incidents"], [])
        self.assertTrue(body["audit_chain_valid"])
        # Audit log has the entries and the chain verifies.
        status, body = self.api.get("/audit")
        self.assertEqual(status, 200, body)
        self.assertTrue(body["chain_valid"])
        actions = [e["action"] for e in body["entries"]]
        self.assertIn("ACCOUNT_OPENED", actions)
        self.assertIn("INCIDENT", actions)
        self.assertIn("RECOVERED", actions)

    def test_null_target_rejected_then_fallback_over_http(self):
        self.api.post("/accounts", {
            "account_id": "BANK-1", "account_type": "bank",
            "owner": "Bank", "balance": 1_000.00, "currency": "USD",
        })
        self.api.post("/incidents", {
            "victim_id": "BANK-1", "amount_stolen": 200.00,
            "amount_frozen": 100.00, "kind": "phishing_transfer",
        })
        # Force a null target: the guard rejects it, the fallback
        # reimburses the capped amount (50.00).
        status, body = self.api.post("/cycles/run",
                                     {"force_null_target": True})
        self.assertEqual(status, 200, body)
        self.assertIsNone(body["agent_target"])
        self.assertIn("None", body["guard"])
        self.assertIsNone(body["agent_recovered"])
        self.assertEqual(body["fallback_recovered"], 1)
        self.assertEqual(body["pool_balance"], 50.00)

    def test_bad_requests_are_400(self):
        status, body = self.api.post("/accounts", {"account_id": "X"})
        self.assertEqual(status, 400, body)
        status, body = self.api.post("/incidents", {
            "victim_id": "NO-SUCH-ACCOUNT", "amount_stolen": 1.00,
            "amount_frozen": 1.00,
        })
        self.assertEqual(status, 400, body)

    def test_transfer_to_external_address_over_http(self):
        self.api.post("/accounts", {
            "account_id": "BANK-1", "account_type": "bank",
            "owner": "Bank", "balance": 1_000.00, "currency": "USD",
        })
        status, body = self.api.post("/transfers", {
            "from_account_id": "BANK-1",
            "to_address": "0xAbC123def456",
            "amount": 250.00,
        })
        self.assertEqual(status, 201, body)
        self.assertEqual(body["transfer_id"], 1)
        self.assertEqual(body["to"], "0xAbC123def456")
        self.assertFalse(body["internal"])
        # The source balance dropped and the transfer is listed.
        status, body = self.api.get("/summary")
        self.assertEqual(body["accounts"]["BANK-1"]["balance"], 750.00)
        self.assertEqual(len(body["transfers"]), 1)
        self.assertEqual(body["total_transferred"], 250.00)
        status, body = self.api.get("/transfers")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["transfers"][0]["to"], "0xAbC123def456")
        # An over-balance transfer is rejected.
        status, body = self.api.post("/transfers", {
            "from_account_id": "BANK-1",
            "to_address": "0xabc",
            "amount": 10_000.00,
        })
        self.assertEqual(status, 400, body)


class TestRecoveryAPIAuth(unittest.TestCase):
    """Basic Auth enforcement: 401 without/wrong creds, 200 with them."""

    def setUp(self):
        self.server = serve("127.0.0.1", 0, engine=RecoveryEngine(),
                            auth=("testuser", "testpass"))
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def _get(self, path, user=None, password=None):
        req = urllib.request.Request(self.base + path)
        if user is not None:
            token = base64.b64encode(
                f"{user}:{password}".encode()).decode()
            req.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_no_credentials_is_401(self):
        status, body = self._get("/health")
        self.assertEqual(status, 401, body)
        self.assertIn("error", body)

    def test_wrong_credentials_is_401(self):
        status, body = self._get("/health", "testuser", "wrongpass")
        self.assertEqual(status, 401, body)
        status, body = self._get("/health", "wronguser", "testpass")
        self.assertEqual(status, 401, body)

    def test_correct_credentials_is_200(self):
        status, body = self._get("/health", "testuser", "testpass")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "ok")


class TestDashboardAndSense(unittest.TestCase):
    def setUp(self):
        self.server = serve("127.0.0.1", 0, engine=RecoveryEngine(),
                            auth=False)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.api = _Client(f"http://127.0.0.1:{self.port}")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_dashboard_serves_html(self):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/dashboard") as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn("text/html", resp.headers["Content-Type"])
                body = resp.read().decode()
        except urllib.error.HTTPError as exc:
            self.fail(f"/dashboard returned HTTP {exc.code}")
        self.assertIn("Cyber CIV Recovery", body)
        self.assertIn("Sense a signal", body)
        self.assertIn("/summary", body)

    def test_sense_bank_signal_over_http(self):
        status, body = self.api.post(
            "/sense", {"signal": "bank:021000021:123456789"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["signal_type"], "bank_account")
        self.assertEqual(body["resolved"]["routing_number"], "021000021")
        kinds = [s["kind"] for s in body["signals"]]
        self.assertIn("BANK_SIGNAL_VALID", kinds)

    def test_sense_missing_signal_is_400(self):
        status, body = self.api.post("/sense", {})
        self.assertEqual(status, 400, body)
        self.assertIn("error", body)

    def test_sense_does_not_change_state(self):
        # Sensing is read-only: it must not create incidents or
        # change the engine, and it must not persist.
        status, before = self.api.get("/summary")
        self.assertEqual(status, 200)
        status, body = self.api.post(
            "/sense", {"signal": "ref:WIRE-2026-1101-8842"})
        self.assertEqual(status, 200, body)
        status, after = self.api.get("/summary")
        self.assertEqual(status, 200)
        self.assertEqual(after["cycle"], before["cycle"])
        self.assertEqual(after["total_recovered"], before["total_recovered"])
        self.assertEqual(after["audit_entries"], before["audit_entries"])


if __name__ == "__main__":
    unittest.main()
