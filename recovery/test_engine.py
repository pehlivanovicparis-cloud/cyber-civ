"""Unit tests for the Cyber CIV Recovery subsystem engine.

Run:  python3 -m recovery.cli test
       python3 -m unittest discover -s recovery -p "test_*.py" -t .
"""

import unittest

from recovery.engine import Account, Guard, RecoveryAgent, RecoveryEngine


def _engine() -> RecoveryEngine:
    e = RecoveryEngine(fallback_cap=50.0, guard_cooldown_cycles=1)
    e.add_account(Account("BANK-1", "bank", "Bank", 1_000.00, "USD"))
    e.add_account(Account("CRYPTO-1", "crypto", "Wallet", 10.00, "ETH"))
    return e


class TestGuard(unittest.TestCase):
    def test_rejects_null_target(self):
        e = _engine()
        e.report_incident("BANK-1", 200.00, 100.00)
        d = e.guard.validate(None, 100.00, 1, e.open_by_victim, e.pool)
        self.assertFalse(d.accepted)
        self.assertIn("None", d.reason)

    def test_rejects_no_open_loss(self):
        e = _engine()
        d = e.guard.validate("BANK-1", 100.00, 1, e.open_by_victim, e.pool)
        self.assertFalse(d.accepted)
        self.assertIn("no open loss", d.reason)

    def test_rejects_amount_over_frozen(self):
        e = _engine()
        e.report_incident("BANK-1", 200.00, 100.00)
        d = e.guard.validate("BANK-1", 150.00, 1, e.open_by_victim, e.pool)
        self.assertFalse(d.accepted)
        self.assertIn("exceeds frozen", d.reason)

    def test_rejects_pool_insufficient(self):
        e = _engine()
        e.report_incident("BANK-1", 200.00, 100.00)
        e.pool.balance = 10.00  # drain the pool below the frozen amount
        d = e.guard.validate("BANK-1", 100.00, 1, e.open_by_victim, e.pool)
        self.assertFalse(d.accepted)
        self.assertIn("pool insufficient", d.reason)

    def test_enforces_cooldown(self):
        e = _engine()
        e.report_incident("BANK-1", 200.00, 100.00)
        e.report_incident("CRYPTO-1", 4.00, 2.00)
        # First recovery succeeds and starts the cooldown.
        r1 = e.run_cycle()
        self.assertIsNotNone(r1["agent_recovered"])
        # Next cycle the guard must reject (cooldown 1 cycle).
        d = e.guard.validate("CRYPTO-1", 2.00, e.cycle + 1,
                             e.open_by_victim, e.pool)
        self.assertFalse(d.accepted)
        self.assertIn("cooldown", d.reason)


class TestAgentAndFallback(unittest.TestCase):
    def test_agent_recovers_valid_victim(self):
        e = _engine()
        e.report_incident("BANK-1", 200.00, 100.00)
        before = e.accounts["BANK-1"].balance  # 800.00 after the theft
        r = e.run_cycle()
        self.assertEqual(r["agent_recovered"], 1)
        self.assertEqual(e.accounts["BANK-1"].balance,
                         round(before + 100.00, 2))
        self.assertEqual(e.incidents[1].status, "closed")
        self.assertEqual(e.pool.balance, 0.0)

    def test_fallback_covers_null_target_failure(self):
        e = _engine()
        e.report_incident("BANK-1", 200.00, 100.00)
        # Agent fails with a null target; the guard rejects it.
        r = e.run_cycle(force_null_target=True)
        self.assertIsNone(r["agent_target"])
        self.assertIn("None", r["guard"])
        self.assertIsNone(r["agent_recovered"])
        # The deterministic fallback still reimburses (capped at 50.00).
        self.assertEqual(r["fallback_recovered"], 1)
        self.assertEqual(e.incidents[1].amount_recovered, 50.00)
        self.assertEqual(e.incidents[1].remaining_frozen, 50.00)

    def test_fallback_capped_per_cycle(self):
        e = _engine()
        e.report_incident("BANK-1", 500.00, 400.00)
        e.run_cycle(force_null_target=True)  # fallback pays 50.00 (cap)
        self.assertEqual(e.incidents[1].amount_recovered, 50.00)
        e.run_cycle(force_null_target=True)  # fallback pays another 50.00
        self.assertEqual(e.incidents[1].amount_recovered, 100.00)

    def test_fallback_oldest_first(self):
        e = _engine()
        e.report_incident("BANK-1", 200.00, 100.00)   # incident 1 (oldest)
        e.report_incident("CRYPTO-1", 4.00, 2.00)     # incident 2
        r = e.run_cycle(force_null_target=True)
        self.assertEqual(r["fallback_recovered"], 1)  # oldest first


class TestAuditAndPool(unittest.TestCase):
    def test_audit_chain_valid_and_tamper_detected(self):
        e = _engine()
        e.report_incident("BANK-1", 200.00, 100.00)
        e.run_cycle()
        self.assertTrue(e.log.verify())
        # Tamper with an entry; the chain must break.
        e.log.entries[0]["details"]["balance"] = 999_999.00
        self.assertFalse(e.log.verify())

    def test_pool_conserved(self):
        e = _engine()
        e.report_incident("BANK-1", 200.00, 100.00)
        e.report_incident("CRYPTO-1", 4.00, 2.00)
        frozen_total = 100.00 + 2.00
        self.assertEqual(e.pool.balance, frozen_total)
        e.run_cycle()  # agent recovers the largest (BANK-1, 100.00)
        e.run_cycle(force_null_target=True)  # fallback covers CRYPTO-1 (2.00)
        recovered = sum(i.amount_recovered for i in e.incidents.values())
        self.assertEqual(round(e.pool.balance + recovered, 2), frozen_total)
        self.assertEqual(e.pool.balance, 0.0)

    def test_incident_drains_and_freezes(self):
        e = _engine()
        inc = e.report_incident("BANK-1", 200.00, 100.00,
                                kind="phishing_transfer")
        self.assertEqual(e.accounts["BANK-1"].balance, 800.00)
        self.assertEqual(inc.amount_stolen, 200.00)
        self.assertEqual(inc.amount_frozen, 100.00)
        self.assertEqual(inc.amount_lost, 100.00)
        self.assertEqual(inc.status, "open")
        self.assertEqual(e.pool.balance, 100.00)

    def test_recovery_closes_incident(self):
        e = _engine()
        e.report_incident("BANK-1", 200.00, 100.00)
        e.run_cycle()
        self.assertEqual(e.incidents[1].status, "closed")
        self.assertNotIn("BANK-1", e.open_by_victim)


class TestRecoveryWallet(unittest.TestCase):
    def test_wallet_exists_from_start(self):
        e = _engine()
        self.assertIn("RECOVERY-WALLET", e.accounts)
        wallet = e.accounts["RECOVERY-WALLET"]
        self.assertEqual(wallet.account_type, "recovery")
        self.assertEqual(wallet.balance, 0.0)
        self.assertEqual(e.wallet_total_deposited, 0.0)
        self.assertEqual(e.wallet_total_released, 0.0)

    def test_recovery_routes_through_wallet(self):
        e = _engine()
        e.report_incident("BANK-1", 200.00, 100.00)
        e.run_cycle()  # agent recovers the $100
        # The rescued funds passed through the wallet: deposited then
        # released, so the wallet balance returns to 0 but the
        # cumulative totals show the flow.
        self.assertEqual(e.accounts["RECOVERY-WALLET"].balance, 0.0)
        self.assertEqual(e.wallet_total_deposited, 100.00)
        self.assertEqual(e.wallet_total_released, 100.00)
        # The victim still gets paid in full, and the pool drains.
        self.assertEqual(e.accounts["BANK-1"].balance, 900.00)
        self.assertEqual(e.pool.balance, 0.0)
        # Both steps are in the audit log.
        actions = [entry["action"] for entry in e.log.entries]
        self.assertIn("WALLET_DEPOSIT", actions)
        self.assertIn("RECOVERED", actions)

    def test_wallet_totals_accumulate_across_recoveries(self):
        e = _engine()
        e.report_incident("BANK-1", 200.00, 100.00)
        e.report_incident("CRYPTO-1", 4.00, 2.00)
        e.run_cycle()  # agent recovers BANK-1 (100.00)
        e.run_cycle(force_null_target=True)  # fallback covers CRYPTO-1 (2.00)
        self.assertEqual(e.wallet_total_deposited, 102.00)
        self.assertEqual(e.wallet_total_released, 102.00)
        self.assertEqual(e.accounts["RECOVERY-WALLET"].balance, 0.0)


class TestTransfers(unittest.TestCase):
    def test_transfer_to_external_address(self):
        e = _engine()
        record = e.transfer("BANK-1", "0xAbC123def456", 250.00)
        self.assertEqual(record["transfer_id"], 1)
        self.assertEqual(record["from"], "BANK-1")
        self.assertEqual(record["to"], "0xAbC123def456")
        self.assertEqual(record["amount"], 250.00)
        self.assertEqual(record["currency"], "USD")
        self.assertFalse(record["internal"])
        self.assertEqual(e.accounts["BANK-1"].balance, 750.00)
        self.assertEqual(len(e.transfers), 1)
        # The transfer is in the audit log.
        actions = [entry["action"] for entry in e.log.entries]
        self.assertIn("TRANSFER", actions)

    def test_transfer_to_another_account_is_internal(self):
        e = _engine()
        record = e.transfer("BANK-1", "CRYPTO-1", 100.00)
        self.assertTrue(record["internal"])
        self.assertEqual(e.accounts["BANK-1"].balance, 900.00)
        self.assertEqual(e.accounts["CRYPTO-1"].balance, 110.00)

    def test_transfer_rejects_unknown_source(self):
        e = _engine()
        with self.assertRaises(ValueError):
            e.transfer("NO-SUCH-ACCOUNT", "0xabc", 10.00)

    def test_transfer_rejects_empty_destination(self):
        e = _engine()
        with self.assertRaises(ValueError):
            e.transfer("BANK-1", "   ", 10.00)

    def test_transfer_rejects_non_positive_amount(self):
        e = _engine()
        with self.assertRaises(ValueError):
            e.transfer("BANK-1", "0xabc", 0.00)
        with self.assertRaises(ValueError):
            e.transfer("BANK-1", "0xabc", -5.00)

    def test_transfer_rejects_insufficient_balance(self):
        e = _engine()
        with self.assertRaises(ValueError):
            e.transfer("BANK-1", "0xabc", 10_000.00)
        # Balance is unchanged after the rejected transfer.
        self.assertEqual(e.accounts["BANK-1"].balance, 1_000.00)

    def test_summary_includes_transfers(self):
        e = _engine()
        e.transfer("BANK-1", "0xabc", 100.00)
        e.transfer("CRYPTO-1", "0xdef", 1.00)
        s = e.summary()
        self.assertEqual(len(s["transfers"]), 2)
        self.assertEqual(s["total_transferred"], 101.00)


class TestFindAccountByAddress(unittest.TestCase):
    def test_finds_account_by_address(self):
        engine = RecoveryEngine()
        engine.add_account(Account(
            "CRYPTO-1", "crypto", "Alice", 20.0, "ETH",
            address="0xAbC1234567890123456789012345678901234567"))
        found = engine.find_account_by_address(
            "0xabc1234567890123456789012345678901234567")
        self.assertIsNotNone(found)
        self.assertEqual(found.account_id, "CRYPTO-1")

    def test_returns_none_for_unknown_address(self):
        engine = RecoveryEngine()
        engine.add_account(Account(
            "CRYPTO-1", "crypto", "Alice", 20.0, "ETH",
            address="0xAbC1234567890123456789012345678901234567"))
        self.assertIsNone(engine.find_account_by_address(
            "0x0000000000000000000000000000000000000000"))
        self.assertIsNone(engine.find_account_by_address(""))
        self.assertIsNone(engine.find_account_by_address(None))

    def test_account_without_address_is_not_matched(self):
        engine = RecoveryEngine()
        engine.add_account(Account(
            "BANK-1", "bank", "Bob", 1000.0, "USD"))
        self.assertIsNone(engine.find_account_by_address(
            "0xAbC1234567890123456789012345678901234567"))


if __name__ == "__main__":
    unittest.main()
