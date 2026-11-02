"""Cyber CIV — Recovery subsystem, state persistence.

Saves and loads the full engine state to a JSON file so the service
survives a restart. Before this, state lived only in memory — a
server restart wiped every account, incident, transfer, and audit
entry. Now the service loads state.json on startup and saves it
after every mutation, so the recovery service is actually usable.

The state file is written atomically (temp file + os.replace), so a
crash mid-write cannot corrupt the last good state. The audit log's
hash chain is preserved in the snapshot, so the chain still verifies
after a reload.

The state file holds live operational data (account balances,
incident history, the audit log). It is git-ignored and must not be
committed.

Default location: recovery/state.json (next to this module).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from .engine import RecoveryEngine

DEFAULT_STATE_PATH = Path(__file__).resolve().parent / "state.json"


def save_state(engine: RecoveryEngine, path: Optional[Path] = None) -> Path:
    """Serialize the engine to JSON, written atomically.

    Writes to a temp file in the same directory, then renames it
    over the state file, so a crash cannot leave a half-written
    state behind. Returns the path written.
    """
    target = Path(path) if path is not None else DEFAULT_STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(engine.to_dict(), indent=2, sort_keys=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(target.parent), prefix=target.name + ".tmp."
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def load_state(path: Optional[Path] = None) -> Optional[RecoveryEngine]:
    """Load an engine from a state file. Returns None if no state
    file exists (fresh start). Raises ValueError on a corrupt or
    unsupported state file — it does not silently start empty."""
    target = Path(path) if path is not None else DEFAULT_STATE_PATH
    if not target.exists():
        return None
    data = json.loads(target.read_text(encoding="utf-8"))
    return RecoveryEngine.from_dict(data)
