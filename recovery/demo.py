"""Demo: Cyber CIV Recovery subsystem.

Scenario: three accounts (a bank, a crypto wallet, a fintech app).
Two cyber incidents freeze funds. The RecoveryAgent (Aegis) tries to
recover them; the guard rejects bad proposals; the deterministic
fallback covers whatever the agent misses.

Run:  python3 -m recovery.cli demo
"""

from .engine import Account, RecoveryEngine


def main() -> None:
    engine = RecoveryEngine(fallback_cap=50.0, guard_cooldown_cycles=1)

    # -- accounts ---------------------------------------------------------
    engine.add_account(Account("BANK-1001", "bank", "First National Bank",
                               10_000.00, "USD"))
    engine.add_account(Account("CRYPTO-0xABC", "crypto", "Treasury Wallet",
                               25.00, "ETH"))
    engine.add_account(Account("FIN-5001", "fintech", "PayFlow User 5001",
                               2_500.00, "USD"))

    print("=== Cyber CIV Recovery — demo ===\n")
    print("Accounts opened:")
    for aid, a in engine.accounts.items():
        print(f"  {aid} ({a.account_type}, {a.owner}): "
              f"{a.balance:,.2f} {a.currency}")
    print(f"  Recovery pool: {engine.pool.balance:,.2f}\n")

    # -- incidents ----------------------------------------------------------
    print("--- Cyber incidents ---")
    # Phishing attack drains the bank account; part is frozen.
    inc1 = engine.report_incident(
        "BANK-1001", amount_stolen=1_200.00, amount_frozen=600.00,
        kind="phishing_transfer",
    )
    print(f"  Incident #{inc1.incident_id}: phishing drains BANK-1001 of "
          f"$1,200.00; ${inc1.amount_frozen:,.2f} frozen in the pool, "
          f"${inc1.amount_lost:,.2f} lost.")
    # API breach drains the crypto wallet; part is frozen.
    inc2 = engine.report_incident(
        "CRYPTO-0xABC", amount_stolen=4.00, amount_frozen=2.50,
        kind="api_key_breach",
    )
    print(f"  Incident #{inc2.incident_id}: API breach drains CRYPTO-0xABC "
          f"of 4.00 ETH; {inc2.amount_frozen:.2f} ETH frozen in the pool, "
          f"{inc2.amount_lost:.2f} ETH lost.")
    print(f"  Pool now holds: ${engine.pool.balance:,.2f} "
          f"(mixed units shown as pool total)\n")

    # -- recovery cycles ------------------------------------------------------
    print("--- Recovery cycles ---")
    # Cycle 1: agent recovers the largest open loss (BANK-1001, $600).
    r = engine.run_cycle()
    print(f"  Cycle {r['cycle']}: agent target={r['agent_target']} "
          f"guard={r['guard']} agent_recovered={r['agent_recovered']} "
          f"fallback={r['fallback_recovered']} "
          f"open_losses={r['open_losses']} pool={r['pool_balance']:,.2f}")
    # Cycle 2: agent fails with a null target (forced, to show the
    # guard + fallback). The guard rejects it; the fallback covers
    # the oldest open loss (CRYPTO-0xABC, capped at $50.00 per cycle
    # in pool units — here 2.50 ETH is below the cap, so it clears).
    r = engine.run_cycle(force_null_target=True)
    print(f"  Cycle {r['cycle']}: agent target={r['agent_target']} "
          f"guard={r['guard']} agent_recovered={r['agent_recovered']} "
          f"fallback={r['fallback_recovered']} "
          f"open_losses={r['open_losses']} pool={r['pool_balance']:,.2f}")
    # Cycle 3: nothing left open; the agent has no proposal.
    r = engine.run_cycle()
    print(f"  Cycle {r['cycle']}: agent target={r['agent_target']} "
          f"guard={r['guard']} agent_recovered={r['agent_recovered']} "
          f"fallback={r['fallback_recovered']} "
          f"open_losses={r['open_losses']} pool={r['pool_balance']:,.2f}")

    # -- summary --------------------------------------------------------------
    print("\n--- Summary ---")
    s = engine.summary()
    for aid, a in s["accounts"].items():
        print(f"  {aid}: {a['balance']:,.2f} {a['currency']} "
              f"({a['type']}, {a['owner']})")
    print(f"  Pool balance: {s['pool_balance']:,.2f}")
    w = s["recovery_wallet"]
    print(f"  Recovery wallet: balance {w['balance']:,.2f} | "
          f"total deposited {w['total_deposited']:,.2f} | "
          f"total released {w['total_released']:,.2f}")
    print(f"  Total frozen: {s['total_frozen']:,.2f} | "
          f"total recovered: {s['total_recovered']:,.2f}")
    print(f"  Open incidents: {len(s['open_incidents'])}")

    # -- send to an address ---------------------------------------------------
    # The rescued funds are back in the victim's account. Send some of
    # them to an external wallet address (recorded in the transfers
    # ledger and in the audit log).
    print("\n--- Send to an address ---")
    t = engine.transfer("BANK-1001", "0xSafeWallet7f3a9c2e1b", 500.00)
    print(f"  Transfer #{t['transfer_id']}: {t['from']} -> {t['to']} "
          f"{t['amount']:,.2f} {t['currency']} "
          f"(internal={t['internal']})")
    print(f"  BANK-1001 balance now: "
          f"{engine.accounts['BANK-1001'].balance:,.2f} USD")
    print(f"  Total transferred: "
          f"{engine.summary()['total_transferred']:,.2f}")
    print(f"  Audit entries: {s['audit_entries']} | "
          f"hash chain valid: {s['audit_chain_valid']}")
    print("\nAudit trail (last 5 entries):")
    for e in engine.log.entries[-5:]:
        print(f"  #{e['seq']} cycle {e['cycle']} {e['action']}: "
              f"{e['details']} (hash {e['hash'][:12]}…)")


if __name__ == "__main__":
    main()
