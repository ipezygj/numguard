"""onchain_log — a small idempotency index for on-chain writes (anchor / EAS attest).

Anchoring or attesting the same receipt twice would send a second Base transaction: wasted gas from the
operator's wallet and a duplicate on-chain record. This records `(digest, kind) -> result` so a repeat returns
the first result without a new tx. Best-effort (per-instance SQLite; the chain is the ultimate source of truth,
but this makes the common retry/double-call path idempotent and gas-safe).
"""
from __future__ import annotations
import json, os, sqlite3, threading, time
from pathlib import Path

_STORE = Path(os.environ.get("NUMGUARD_ONCHAIN_LOG", Path.home() / ".numguard" / "onchain.db"))
_LOCK = threading.Lock()
_INIT: set = set()


def _conn() -> sqlite3.Connection:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_STORE), timeout=10)
    if str(_STORE) not in _INIT:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("""CREATE TABLE IF NOT EXISTS onchain (
            digest TEXT, kind TEXT, result TEXT, ts INTEGER, PRIMARY KEY (digest, kind))""")
        c.commit()
        _INIT.add(str(_STORE))
    return c


def get(digest: str, kind: str) -> dict | None:
    """The stored result for an already-written (digest, kind), or None."""
    if not digest:
        return None
    c = _conn()
    try:
        row = c.execute("SELECT result FROM onchain WHERE digest=? AND kind=?",
                        (digest.lower(), kind)).fetchone()
    finally:
        c.close()
    return json.loads(row[0]) if row else None


def put(digest: str, kind: str, result: dict) -> None:
    if not digest or not isinstance(result, dict):
        return
    with _LOCK:
        c = _conn()
        try:
            c.execute("INSERT OR REPLACE INTO onchain VALUES (?,?,?,?)",
                      (digest.lower(), kind, json.dumps(result), int(time.time())))
            c.commit()
        finally:
            c.close()


def _selftest():
    import tempfile
    global _STORE
    _STORE = Path(tempfile.mkdtemp()) / "onchain.db"
    _INIT.clear()
    assert get("0xab", "anchor") is None
    put("0xAB", "anchor", {"tx": "0x123"})
    assert get("0xab", "anchor")["tx"] == "0x123"      # case-insensitive digest
    assert get("0xab", "eas") is None                  # kind-scoped
    print("onchain_log selftest: OK (idempotency index, case-insensitive, kind-scoped)")


if __name__ == "__main__":
    _selftest()
