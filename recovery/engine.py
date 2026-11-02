"""
Cyber CIV — Recovery subsystem, core engine.

A real-world backup / recovery system for banking, crypto, and
fintech accounts, built on the Cyber CIV recovery concepts. This is
the integrated version of the fintech recovery prototype, now part
of the Cyber CIV project as a deployable subsystem.

  Cyber CIV                    This subsystem
  ---------------------------  ------------------------------------------
  Civilization (Ares, Pax...)  Account (Bank, Crypto wallet, Fintech)
  Intent (0-1)                 Balance (USD / token units)
  Theft (steals intent)        Cyber incident (drains funds, freezes some)
  Frozen share                 Frozen funds (held in the Recovery Pool)
  RECOVER action               Recovery action (reimburse victim from pool)
  Aegis (recovery agent)       RecoveryAgent (monitors, proposes RECOVER)
  Guard (validates proposals)  Guard (rejects null/invalid/cooldown)
  Deterministic fallback       Auto-reimburse oldest open loss (capped)
  Recovery pool                Pooled reserve (holds frozen funds)
  Audit log (hash-chained)     AuditLog (SHA-256 hash chain, append-only)
  Recovery wallet              RECOVERY-WALLET account: rescued funds are
                               deposited here from the pool, then released
                               to the victim. Every deposit and release is
                               logged, so the wallet is a fully auditable
                               escrow for rescued funds.
  Send to an address           transfer(): send funds from any account to
                               an external wallet address (or another
                               account). Recorded in a transfers ledger
                               and in the audit log.

Design rules (from the Cyber CIV recovery work):
  - A RECOVER must name a victim with an OPEN loss. A null target is
    rejected by the guard. (This is the null-target failure the
    2026-11-01 injection test measured: the retry recovered only 2/10.)
  - The guard enforces a cooldown after each RECOVER attempt, so the
    agent cannot spam proposals.
  - If no RECOVER succeeds in a cycle, the deterministic fallback
    auto-reimburses the oldest open loss, capped per cycle. Recovery
    is guaranteed to make progress even when the agent fails.
  - The recovery pool is conserved: funds in (frozen) == funds out
    (recovered). No funds are created or destroyed.
  - Every action is written to a hash-chained audit log. Tampering
    with any entry breaks the chain and is detectable.

This is a clean-room prototype. It re-implements the recovery
concepts for the fintech domain; it does not copy Cyber CIV code.
Proprietary work of Paris Pehlivanovic. All rights reserved.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

ACCOUNT_TYPES = ("bank", "crypto", "fintech", "recovery")

# The system Recovery Wallet: rescued funds are deposited here from the
# pool, then released to the victim. It is a real account, so it shows
# up in the summary like any other account, and every deposit/release
# is written to the hash-chained audit log.
RECOVERY_WALLET_ID = "RECOVERY-WALLET"


@dataclass
class Account:
    """A bank account, crypto wallet, or fintech account.

    address: the public identifier the signal monitor uses to sense
    this account — a wallet address for crypto, an account number
    for bank/fintech. Optional. When set, a LARGE_TRANSFER signal
    from this address auto-opens a recovery incident for it.
    """

    account_id: str
    account_type: str  # "bank" | "crypto" | "fintech"
    owner: str
    balance: float
    currency: str  # "USD" for bank/fintech, e.g. "ETH" for crypto
    address: Optional[str] = None

    def __post_init__(self) -> None:
        if self.account_type not in ACCOUNT_TYPES:
            raise ValueError(f"unknown account type: {self.account_type}")
        if self.balance < 0:
            raise ValueError("balance cannot be negative")


# ---------------------------------------------------------------------------
# Incidents (cyber theft / breach / fraud)
# ---------------------------------------------------------------------------

@dataclass
class Incident:
    """A cyber incident that drained funds and froze a portion.

    amount_stolen: total taken from the victim.
    amount_frozen: portion held in the Recovery Pool (recoverable).
    amount_lost:   amount_stolen - amount_frozen (unrecoverable).
    amount_recovered: how much has been reimbursed so far.
    status: "open" while amount_recovered < amount_frozen, else "closed".
    """

    incident_id: int
    victim_id: str
    amount_stolen: float
    amount_frozen: float
    cycle: int
    kind: str = "unauthorized_transfer"
    amount_recovered: float = 0.0

    @property
    def amount_lost(self) -> float:
        return round(self.amount_stolen - self.amount_frozen, 2)

    @property
    def remaining_frozen(self) -> float:
        return round(self.amount_frozen - self.amount_recovered, 2)

    @property
    def status(self) -> str:
        return "closed" if self.remaining_frozen <= 0 else "open"


# ---------------------------------------------------------------------------
# Recovery pool (pooled reserve)
# ---------------------------------------------------------------------------

class RecoveryPool:
    """Holds frozen funds. Pays out recoveries. Conserved by construction.

    Funds enter the pool when an incident freezes them, and leave only
    when a recovery reimburses a victim. The pool never creates funds.
    """

    def __init__(self) -> None:
        self.balance: float = 0.0

    def freeze(self, amount: float) -> None:
        if amount < 0:
            raise ValueError("cannot freeze a negative amount")
        self.balance = round(self.balance + amount, 2)

    def pay(self, amount: float) -> None:
        if amount < 0:
            raise ValueError("cannot pay a negative amount")
        if amount > self.balance + 1e-9:
            raise ValueError(
                f"pool insufficient: tried to pay {amount}, "
                f"pool holds {self.balance}"
            )
        self.balance = round(self.balance - amount, 2)


# ---------------------------------------------------------------------------
# Audit log (hash-chained, append-only)
# ---------------------------------------------------------------------------

class AuditLog:
    """Append-only log. Each entry hashes the previous entry's hash,
    so tampering with any entry breaks the chain and is detectable."""

    def __init__(self) -> None:
        self.entries: List[Dict] = []
        self._prev_hash: str = "GENESIS"

    def record(self, cycle: int, action: str, details: Dict) -> Dict:
        entry = {
            "seq": len(self.entries) + 1,
            "cycle": cycle,
            "action": action,
            "details": details,
            "prev_hash": self._prev_hash,
        }
        payload = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        entry["hash"] = hashlib.sha256(payload.encode()).hexdigest()
        self._prev_hash = entry["hash"]
        self.entries.append(entry)
        return entry

    def verify(self) -> bool:
        """Recompute the chain. Returns True iff no entry was altered."""
        prev = "GENESIS"
        for entry in self.entries:
            check = dict(entry)
            claimed = check.pop("hash")
            check["prev_hash"] = prev
            payload = json.dumps(check, sort_keys=True, separators=(",", ":"))
            if hashlib.sha256(payload.encode()).hexdigest() != claimed:
                return False
            prev = claimed
        return True


# ---------------------------------------------------------------------------
# Guard (validates a RECOVER proposal)
# ---------------------------------------------------------------------------

@dataclass
class GuardDecision:
    accepted: bool
    reason: str


class Guard:
    """Validates a RECOVER proposal before it executes.

    Rejects when:
      - target is None (the null-target failure),
      - target has no open incident,
      - the agent is on cooldown,
      - the amount exceeds the frozen remainder,
      - the pool cannot cover the amount.
    """

    def __init__(self, cooldown_cycles: int = 1) -> None:
        self.cooldown_cycles = cooldown_cycles
        self._last_recover_cycle: Optional[int] = None

    def cooldown_remaining(self, cycle: int) -> int:
        if self._last_recover_cycle is None:
            return 0
        if cycle <= self._last_recover_cycle:
            return self.cooldown_cycles
        # A cooldown of N cycles blocks the N cycles after the attempt.
        # E.g. cooldown=1, attempt in cycle 1 -> blocked in cycle 2,
        # free in cycle 3.
        elapsed = cycle - self._last_recover_cycle
        return max(0, self.cooldown_cycles - elapsed + 1)

    def validate(
        self,
        target_id: Optional[str],
        amount: float,
        cycle: int,
        open_incidents: Dict[str, Incident],
        pool: RecoveryPool,
    ) -> GuardDecision:
        if target_id is None:
            return GuardDecision(False, "bad target None")
        if target_id not in open_incidents:
            return GuardDecision(False, f"no open loss for {target_id}")
        if self.cooldown_remaining(cycle) > 0:
            return GuardDecision(
                False,
                f"RECOVER on cooldown ({self.cooldown_remaining(cycle)} cycle(s) left)",
            )
        incident = open_incidents[target_id]
        if amount > incident.remaining_frozen + 1e-9:
            return GuardDecision(
                False,
                f"amount {amount} exceeds frozen remainder "
                f"{incident.remaining_frozen} for {target_id}",
            )
        if amount > pool.balance + 1e-9:
            return GuardDecision(
                False, f"pool insufficient: {amount} > {pool.balance}"
            )
        return GuardDecision(True, "ok")

    def mark_attempt(self, cycle: int) -> None:
        self._last_recover_cycle = cycle


# ---------------------------------------------------------------------------
# Recovery agent (Aegis)
# ---------------------------------------------------------------------------

class RecoveryAgent:
    """Monitors open incidents and proposes RECOVER actions.

    The agent names the victim with the largest open frozen amount.
    It does NOT decide whether the proposal is valid — the guard does.
    A null-target proposal is a model/agent failure, and the guard
    rejects it (this is the failure the 2026-11-01 test measured).
    """

    def __init__(self, name: str = "Aegis") -> None:
        self.name = name

    def propose(
        self, open_incidents: Dict[str, Incident]
    ) -> tuple[Optional[str], float]:
        """Return (target_id, amount). May return (None, 0.0) to model
        an agent failure (null target), which the guard must reject."""
        if not open_incidents:
            return None, 0.0
        # Largest open frozen amount first.
        victim_id = max(
            open_incidents,
            key=lambda vid: open_incidents[vid].remaining_frozen,
        )
        return victim_id, open_incidents[victim_id].remaining_frozen


# ---------------------------------------------------------------------------
# Engine (orchestrates cycles)
# ---------------------------------------------------------------------------

class RecoveryEngine:
    """Runs the recovery loop over discrete cycles.

    Each cycle:
      1. (Outside the engine) incidents may occur; see report_incident.
      2. The agent proposes a RECOVER. The guard validates it.
         If accepted, the pool pays the victim and the incident is
         updated. If rejected, nothing is paid.
      3. If no RECOVER succeeded this cycle, the deterministic
         fallback auto-reimburses the oldest open loss, capped at
         fallback_cap per cycle.
      4. Every action is written to the audit log.
    """

    def __init__(
        self,
        fallback_cap: float = 50.0,
        guard_cooldown_cycles: int = 1,
    ) -> None:
        self.accounts: Dict[str, Account] = {}
        self.incidents: Dict[int, Incident] = {}
        self.open_by_victim: Dict[str, Incident] = {}
        self.pool = RecoveryPool()
        self.log = AuditLog()
        self.guard = Guard(cooldown_cycles=guard_cooldown_cycles)
        self.agent = RecoveryAgent()
        self.fallback_cap = fallback_cap
        self.cycle = 0
        self._next_incident_id = 1
        # System Recovery Wallet: rescued funds are deposited here from
        # the pool, then released to the victim. It is a real account
        # (shows up in the summary) and every deposit/release is
        # logged. Cumulative totals make the flow visible even though
        # the wallet balance returns to 0 after each release.
        # NOTE: like the pool, the wallet holds a single mixed balance
        # (the prototype treats USD and ETH amounts as generic units);
        # a production system would use one wallet per currency.
        self.accounts[RECOVERY_WALLET_ID] = Account(
            RECOVERY_WALLET_ID, "recovery", "Cyber CIV Recovery Wallet",
            0.0, "USD",
        )
        self.wallet_total_deposited = 0.0
        self.wallet_total_released = 0.0
        # Outbound transfers: funds sent from an account to a
        # destination address (an external wallet address, or another
        # account id for an internal transfer). Each transfer is
        # recorded here and written to the hash-chained audit log.
        self.transfers: List[Dict] = []
        self._next_transfer_id = 1

    # -- setup -------------------------------------------------------------
    def add_account(self, account: Account) -> None:
        if account.account_id in self.accounts:
            raise ValueError(f"duplicate account: {account.account_id}")
        self.accounts[account.account_id] = account
        self.log.record(
            self.cycle,
            "ACCOUNT_OPENED",
            {"account_id": account.account_id,
             "type": account.account_type,
             "owner": account.owner,
             "balance": account.balance,
             "currency": account.currency,
             "address": account.address},
        )

    def find_account_by_address(self, address: str) -> Optional[Account]:
        """Find an account by its public address (wallet address or
        account number). Case-insensitive. Used by the signal monitor
        to map a sensed address to a registered account."""
        if not address:
            return None
        needle = address.strip().lower()
        for account in self.accounts.values():
            if account.address and account.address.strip().lower() == needle:
                return account
        return None

    # -- incidents -----------------------------------------------------------
    def report_incident(
        self,
        victim_id: str,
        amount_stolen: float,
        amount_frozen: float,
        kind: str = "unauthorized_transfer",
    ) -> Incident:
        """Record a cyber incident. Drains the victim's balance by the
        stolen amount; freezes a portion in the recovery pool."""
        if victim_id not in self.accounts:
            raise ValueError(f"unknown victim account: {victim_id}")
        if amount_frozen > amount_stolen + 1e-9:
            raise ValueError("frozen cannot exceed stolen")
        if amount_stolen < 0 or amount_frozen < 0:
            raise ValueError("amounts cannot be negative")
        victim = self.accounts[victim_id]
        if amount_stolen > victim.balance + 1e-9:
            raise ValueError(
                f"incident exceeds victim balance: {amount_stolen} > "
                f"{victim.balance}"
            )
        victim.balance = round(victim.balance - amount_stolen, 2)
        self.pool.freeze(amount_frozen)
        incident = Incident(
            incident_id=self._next_incident_id,
            victim_id=victim_id,
            amount_stolen=round(amount_stolen, 2),
            amount_frozen=round(amount_frozen, 2),
            cycle=self.cycle,
            kind=kind,
        )
        self._next_incident_id += 1
        self.incidents[incident.incident_id] = incident
        # One open incident per victim (latest wins for the map; the
        # full history stays in self.incidents).
        self.open_by_victim[victim_id] = incident
        self.log.record(
            self.cycle,
            "INCIDENT",
            {"incident_id": incident.incident_id,
             "victim": victim_id,
             "kind": kind,
             "stolen": incident.amount_stolen,
             "frozen": incident.amount_frozen,
             "lost": incident.amount_lost,
             "pool_balance": self.pool.balance},
        )
        return incident

    # -- recovery ------------------------------------------------------------
    def _execute_recovery(
        self, incident: Incident, amount: float, source: str
    ) -> None:
        """Pay the victim from the pool, routed through the Recovery
        Wallet. Pool -> Wallet (deposit) -> Victim (release). Both
        steps are logged, so the wallet is a fully auditable escrow:
        the rescued funds are deposited to the wallet, then released
        to the victim in the same recovery. The victim is still paid
        immediately; the wallet balance returns to 0 after each
        release, while wallet_total_deposited / wallet_total_released
        track the cumulative flow."""
        self.pool.pay(amount)
        wallet = self.accounts[RECOVERY_WALLET_ID]
        wallet.balance = round(wallet.balance + amount, 2)
        self.wallet_total_deposited = round(
            self.wallet_total_deposited + amount, 2
        )
        self.log.record(
            self.cycle,
            "WALLET_DEPOSIT",
            {"incident_id": incident.incident_id,
             "victim": incident.victim_id,
             "amount": amount,
             "source": source,
             "wallet": RECOVERY_WALLET_ID,
             "wallet_balance": wallet.balance,
             "pool_balance": self.pool.balance},
        )
        wallet.balance = round(wallet.balance - amount, 2)
        self.wallet_total_released = round(
            self.wallet_total_released + amount, 2
        )
        victim = self.accounts[incident.victim_id]
        victim.balance = round(victim.balance + amount, 2)
        incident.amount_recovered = round(incident.amount_recovered + amount, 2)
        if incident.status == "closed":
            self.open_by_victim.pop(incident.victim_id, None)
        self.log.record(
            self.cycle,
            "RECOVERED",
            {"incident_id": incident.incident_id,
             "victim": incident.victim_id,
             "amount": amount,
             "source": source,
             "via_wallet": RECOVERY_WALLET_ID,
             "remaining_frozen": incident.remaining_frozen,
             "pool_balance": self.pool.balance},
        )

    def _fallback(self) -> Optional[Incident]:
        """Deterministic fallback: auto-reimburse the oldest open loss,
        capped at fallback_cap per cycle. Runs only when no RECOVER
        succeeded this cycle. Guarantees progress even if the agent
        fails every time."""
        if not self.open_by_victim:
            return None
        oldest = min(
            self.open_by_victim.values(), key=lambda inc: inc.incident_id
        )
        amount = min(oldest.remaining_frozen, self.fallback_cap)
        if amount <= 0:
            return None
        self._execute_recovery(oldest, amount, source="deterministic_fallback")
        return oldest

    # -- transfers -----------------------------------------------------------
    def transfer(
        self, from_account_id: str, to_address: str, amount: float
    ) -> Dict:
        """Send funds from an account to a destination address.

        to_address is an external wallet address (e.g. a crypto
        address like "0x...") or another account id in this system
        for an internal transfer. The funds leave the source account;
        for an external address they leave the system (recorded here
        and in the audit log). For an internal transfer the destination
        account is credited. Every transfer is written to the
        hash-chained audit log, so the flow is fully traceable.

        NOTE: this is the prototype — a transfer moves balances
        inside this system. It does not broadcast a real on-chain
        transaction. A production deployment would hand the recorded
        transfer to a blockchain / banking rail to execute it.
        """
        if from_account_id not in self.accounts:
            raise ValueError(f"unknown source account: {from_account_id}")
        if not to_address or not str(to_address).strip():
            raise ValueError("destination address is required")
        to_address = str(to_address).strip()
        if amount <= 0:
            raise ValueError("transfer amount must be positive")
        source = self.accounts[from_account_id]
        if amount > source.balance + 1e-9:
            raise ValueError(
                f"insufficient balance: tried to send {amount}, "
                f"{from_account_id} holds {source.balance}"
            )
        amount = round(amount, 2)
        source.balance = round(source.balance - amount, 2)
        internal = to_address in self.accounts
        if internal:
            dest = self.accounts[to_address]
            dest.balance = round(dest.balance + amount, 2)
        record = {
            "transfer_id": self._next_transfer_id,
            "from": from_account_id,
            "to": to_address,
            "amount": amount,
            "currency": source.currency,
            "internal": internal,
            "cycle": self.cycle,
        }
        self._next_transfer_id += 1
        self.transfers.append(record)
        self.log.record(
            self.cycle,
            "TRANSFER",
            {"transfer_id": record["transfer_id"],
             "from": from_account_id,
             "to": to_address,
             "amount": amount,
             "currency": source.currency,
             "internal": internal},
        )
        return dict(record)

    # -- cycle -----------------------------------------------------------------
    def run_cycle(self, force_null_target: bool = False) -> Dict:
        """Run one recovery cycle.

        force_null_target: if True, the agent proposes a null target
        (models the agent failure). The guard must reject it, and the
        fallback then covers the loss. Used by the demo to show the
        guard + fallback working.
        """
        self.cycle += 1
        recovered: Optional[Incident] = None
        guard_reason = ""
        if force_null_target:
            target_id, amount = None, 0.0
        else:
            target_id, amount = self.agent.propose(self.open_by_victim)
        decision = self.guard.validate(
            target_id, amount, self.cycle, self.open_by_victim, self.pool
        )
        if decision.accepted:
            assert target_id is not None
            incident = self.open_by_victim[target_id]
            self.guard.mark_attempt(self.cycle)
            self._execute_recovery(incident, amount, source="agent")
            recovered = incident
            guard_reason = "accepted"
        else:
            guard_reason = decision.reason
            self.log.record(
                self.cycle,
                "RECOVER_REJECTED",
                {"target": target_id,
                 "amount": amount,
                 "reason": decision.reason},
            )
        fallback_incident = None
        if recovered is None:
            fallback_incident = self._fallback()
        return {
            "cycle": self.cycle,
            "agent_target": target_id,
            "guard": guard_reason,
            "agent_recovered": recovered.incident_id if recovered else None,
            "fallback_recovered": (
                fallback_incident.incident_id if fallback_incident else None
            ),
            "open_losses": len(self.open_by_victim),
            "pool_balance": self.pool.balance,
        }

    # -- reporting ---------------------------------------------------------------
    def summary(self) -> Dict:
        wallet = self.accounts[RECOVERY_WALLET_ID]
        return {
            "cycle": self.cycle,
            "accounts": {
                aid: {"type": a.account_type, "owner": a.owner,
                      "balance": a.balance, "currency": a.currency,
                      "address": a.address}
                for aid, a in self.accounts.items()
            },
            "pool_balance": self.pool.balance,
            "recovery_wallet": {
                "account_id": RECOVERY_WALLET_ID,
                "balance": wallet.balance,
                "total_deposited": self.wallet_total_deposited,
                "total_released": self.wallet_total_released,
            },
            "open_incidents": [
                {"id": inc.incident_id, "victim": inc.victim_id,
                 "stolen": inc.amount_stolen, "frozen": inc.amount_frozen,
                 "recovered": inc.amount_recovered,
                 "remaining": inc.remaining_frozen, "status": inc.status}
                for inc in self.incidents.values()
                if inc.status == "open"
            ],
            "total_recovered": round(
                sum(i.amount_recovered for i in self.incidents.values()), 2
            ),
            "total_frozen": round(
                sum(i.amount_frozen for i in self.incidents.values()), 2
            ),
            "transfers": [dict(t) for t in self.transfers],
            "total_transferred": round(
                sum(t["amount"] for t in self.transfers), 2
            ),
            "audit_entries": len(self.log.entries),
            "audit_chain_valid": self.log.verify(),
        }

    # -- persistence -----------------------------------------------------------
    def to_dict(self) -> Dict:
        """Serialize the full engine state to a JSON-able dict.

        Includes accounts, incidents, pool, wallet totals, transfers,
        audit log (entries + chain head), cycle, counters, and guard
        state — everything needed to resume exactly where this
        engine left off after a restart.
        """
        return {
            "version": 1,
            "cycle": self.cycle,
            "fallback_cap": self.fallback_cap,
            "guard_cooldown_cycles": self.guard.cooldown_cycles,
            "guard_last_recover_cycle": self.guard._last_recover_cycle,
            "agent_name": self.agent.name,
            "accounts": {
                aid: {
                    "account_type": a.account_type,
                    "owner": a.owner,
                    "balance": a.balance,
                    "currency": a.currency,
                    "address": a.address,
                }
                for aid, a in self.accounts.items()
            },
            "incidents": {
                str(iid): {
                    "victim_id": inc.victim_id,
                    "amount_stolen": inc.amount_stolen,
                    "amount_frozen": inc.amount_frozen,
                    "amount_recovered": inc.amount_recovered,
                    "cycle": inc.cycle,
                    "kind": inc.kind,
                }
                for iid, inc in self.incidents.items()
            },
            "open_by_victim": {
                vid: inc.incident_id
                for vid, inc in self.open_by_victim.items()
            },
            "pool_balance": self.pool.balance,
            "wallet_total_deposited": self.wallet_total_deposited,
            "wallet_total_released": self.wallet_total_released,
            "transfers": [dict(t) for t in self.transfers],
            "next_incident_id": self._next_incident_id,
            "next_transfer_id": self._next_transfer_id,
            "audit_entries": [dict(e) for e in self.log.entries],
            "audit_prev_hash": self.log._prev_hash,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "RecoveryEngine":
        """Rebuild an engine from a to_dict() snapshot."""
        if data.get("version") != 1:
            raise ValueError(
                f"unsupported state version: {data.get('version')}"
            )
        engine = cls(
            fallback_cap=float(data.get("fallback_cap", 50.0)),
            guard_cooldown_cycles=int(
                data.get("guard_cooldown_cycles", 1)
            ),
        )
        engine.cycle = int(data.get("cycle", 0))
        engine.agent.name = str(data.get("agent_name", "Aegis"))
        engine.guard._last_recover_cycle = data.get(
            "guard_last_recover_cycle"
        )
        engine.accounts = {}
        for aid, a in data.get("accounts", {}).items():
            engine.accounts[aid] = Account(
                account_id=aid,
                account_type=a["account_type"],
                owner=a["owner"],
                balance=float(a["balance"]),
                currency=a["currency"],
                address=a.get("address"),
            )
        engine.incidents = {}
        for iid, inc in data.get("incidents", {}).items():
            engine.incidents[int(iid)] = Incident(
                incident_id=int(iid),
                victim_id=inc["victim_id"],
                amount_stolen=float(inc["amount_stolen"]),
                amount_frozen=float(inc["amount_frozen"]),
                cycle=int(inc["cycle"]),
                kind=inc.get("kind", "unauthorized_transfer"),
                amount_recovered=float(inc.get("amount_recovered", 0.0)),
            )
        engine.open_by_victim = {}
        for vid, iid in data.get("open_by_victim", {}).items():
            inc = engine.incidents.get(int(iid))
            if inc is not None:
                engine.open_by_victim[vid] = inc
        engine.pool.balance = float(data.get("pool_balance", 0.0))
        engine.wallet_total_deposited = float(
            data.get("wallet_total_deposited", 0.0)
        )
        engine.wallet_total_released = float(
            data.get("wallet_total_released", 0.0)
        )
        engine.transfers = [dict(t) for t in data.get("transfers", [])]
        engine._next_incident_id = int(data.get("next_incident_id", 1))
        engine._next_transfer_id = int(data.get("next_transfer_id", 1))
        engine.log.entries = [dict(e) for e in data.get("audit_entries", [])]
        engine.log._prev_hash = str(data.get("audit_prev_hash", "GENESIS"))
        return engine
