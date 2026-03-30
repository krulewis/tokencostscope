# SYNC: scripts/calibration_store.py -- library functions only (no CLI)
"""calibration_store.py — Thin storage helper for tokencast calibration data.

Design principle (E2): Concentration, not abstraction. All calibration reads and writes
flow through this module so a future enterprise adapter replaces one file, not many.
No abstract base classes, protocols, or dependency injection — just plain functions.

CLI usage (called by learn.sh):
    python3 calibration_store.py append-history --history PATH --factors PATH --record JSON
    python3 calibration_store.py read-history --history PATH
"""

import json
import os
import tempfile
from pathlib import Path


def read_history(history_path: str) -> list:
    """Read all records from history.jsonl. Skip malformed lines.
    Returns empty list if file absent.
    """
    records = []
    if not Path(history_path).exists():
        return records
    with open(history_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def append_history(history_path: str, record: dict) -> None:
    """Append one record to history.jsonl. Creates file and parent dirs if absent."""
    os.makedirs(os.path.dirname(history_path) or ".", exist_ok=True)
    with open(history_path, "a") as f:
        f.write(json.dumps(record) + "\n")


def read_factors(factors_path: str) -> dict:
    """Read factors.json. Returns {} if absent or malformed."""
    if not Path(factors_path).exists():
        return {}
    try:
        with open(factors_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def write_factors(factors_path: str, factors: dict) -> None:
    """Write factors.json atomically via temp file + rename."""
    dir_path = os.path.dirname(factors_path) or "."
    os.makedirs(dir_path, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(factors, f, indent=2)
            f.write("\n")
        os.replace(tmp, factors_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
