"""commitments — hold a backtest promise over time, at O(1) per observation, backed by SQLite.

An agent OPENS a commitment (its claimed per-period Sharpe), then REPORTS live returns as they arrive. numguard
folds each return into running sufficient statistics (Welford; it NEVER stores the raw returns) and can judge
HELD / DECAYED / BROKEN at any time from ~5 numbers. No background compute — updates happen only when an agent
pushes data — and each report touches ONE row (O(1)), so tracking many agents for years is cheap.

Storage is SQLite: atomic per-row updates (vs rewriting a whole JSON file), a hard cap on rows (oldest
evicted), and durable IF `NUMGUARD_COMMITMENTS` points at persistent storage (a mounted disk / external volume);
on an ephemeral filesystem it resets on redeploy — point it at a durable path or an external DB for permanence.

HONEST LIMIT: this judges the returns an agent REPORTS — a consistency check against its claim, NOT proof of
real performance (see `data_source: self_reported`). Bonding/staking on it was deliberately NOT built.
"""
from __future__ import annotations
import hashlib, hmac, json, os, secrets, sqlite3, threading, time
from pathlib import Path

from . import forward as _forward, receipt as _rcpt, _limits

MAX_COMMITMENTS = 50_000
_STORE = Path(os.environ.get("NUMGUARD_COMMITMENTS", Path.home() / ".numguard" / "commitments.db"))
_LOCK = threading.Lock()
_INIT: set = set()


def owner_hash(api_key: str) -> str:
    """A non-reversible owner tag — so a leaked commitment_id alone can't poison or read a track record."""
    return hashlib.sha256(("numguard-owner:" + str(api_key)).encode()).hexdigest()[:32]


def _conn() -> sqlite3.Connection:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_STORE), timeout=10)
    if str(_STORE) not in _INIT:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("""CREATE TABLE IF NOT EXISTS commitments (
            cid TEXT PRIMARY KEY, claimed_sr REAL, ppy INTEGER, label TEXT, owner TEXT,
            state TEXT, opened_at INTEGER, updated_at INTEGER)""")
        c.commit()
        _INIT.add(str(_STORE))
    return c


def _clean_returns(xs, cap: int = _limits.MAX_ITEMS):
    if not isinstance(xs, (list, tuple)) or len(xs) > cap:
        raise ValueError(f"new_returns must be a list of <= {cap} numbers")
    out = []
    for v in xs:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError("new_returns must contain only numbers")
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            raise ValueError("new_returns must be finite")
        out.append(f)
    return out


def _owned(row_owner: str, owner: str) -> bool:
    return (not row_owner) or hmac.compare_digest(row_owner, owner or "")


def _verdict(cid: str, claimed_sr: float, ppy: int, label: str, state: dict) -> dict:
    n, mean, sd, sk, ku = _forward.stream_stats(state)
    if n < 8:
        return {"commitment_id": cid, "observations": n, "claimed_sharpe": claimed_sr,
                "verdict_label": "PENDING", "survives": None, "data_source": "self_reported",
                "verdict": f"PENDING — {n} live observations so far; need >= 8 to judge. Keep reporting."}
    v = _forward.reconcile_from_stats(claimed_sr, n, mean, sd, sk, ku, periods_per_year=ppy)
    v["commitment_id"] = cid
    v["observations"] = n
    if label:
        v["label"] = label
    return v


def open_commitment(claimed_sr: float, periods_per_year: int = 252, label: str = "", owner: str = "") -> dict:
    """Open a commitment to a claimed per-period Sharpe. Returns a commitment_id to report live returns against.
    `owner` (a hash of the caller's api_key) binds it so only the opener can report/read it."""
    if claimed_sr is None or float(claimed_sr) <= 0:
        raise ValueError("claimed_sr must be a positive per-period Sharpe")
    cid = "cm_" + secrets.token_urlsafe(12)
    now = int(time.time())
    with _LOCK:
        c = _conn()
        try:
            c.execute("INSERT INTO commitments VALUES (?,?,?,?,?,?,?,?)",
                      (cid, float(claimed_sr), int(periods_per_year), str(label)[:80], owner,
                       json.dumps(_forward.stream_init()), now, now))
            # evict oldest beyond the cap (cheap: one DELETE by updated_at)
            c.execute("""DELETE FROM commitments WHERE cid IN (
                SELECT cid FROM commitments ORDER BY updated_at DESC LIMIT -1 OFFSET ?)""", (MAX_COMMITMENTS,))
            c.commit()
        finally:
            c.close()
    return {"commitment_id": cid, "claimed_sharpe": float(claimed_sr), "observations": 0,
            "note": "report live returns to this id with report_returns; numguard folds them at O(1) each."}


def report(commitment_id: str, new_returns, owner: str = "") -> dict:
    """Fold new live returns into the commitment (O(1) each; raw returns are not stored) and return the current
    verdict. Requires the owner that opened it."""
    xs = _clean_returns(new_returns)
    with _LOCK:
        c = _conn()
        try:
            row = c.execute("SELECT claimed_sr,ppy,label,owner,state FROM commitments WHERE cid=?",
                            (commitment_id,)).fetchone()
            if not row:
                return {"error": "unknown commitment_id"}
            claimed_sr, ppy, label, row_owner, state_json = row
            if not _owned(row_owner, owner):
                return {"error": "not your commitment"}
            state = json.loads(state_json)
            for x in xs:
                _forward.stream_update(state, x)
            c.execute("UPDATE commitments SET state=?, updated_at=? WHERE cid=?",
                      (json.dumps(state), int(time.time()), commitment_id))
            c.commit()
        finally:
            c.close()
    return _verdict(commitment_id, claimed_sr, ppy, label, state)


def status(commitment_id: str, owner: str = "") -> dict:
    """The current verdict for a commitment without adding data. Requires the owner that opened it."""
    c = _conn()
    try:
        row = c.execute("SELECT claimed_sr,ppy,label,owner,state FROM commitments WHERE cid=?",
                        (commitment_id,)).fetchone()
    finally:
        c.close()
    if not row:
        return {"error": "unknown commitment_id"}
    claimed_sr, ppy, label, row_owner, state_json = row
    if not _owned(row_owner, owner):
        return {"error": "not your commitment"}
    return _verdict(commitment_id, claimed_sr, ppy, label, json.loads(state_json))


def signed_track_record(commitment_id: str, private_hex: str, public_hex: str = "", owner: str = "") -> dict:
    """Issue a portable, signed (Ed25519) attestation of a commitment's live track record — the accountable
    credential an agent can SHOW. Recomputed server-side; tamper-evident. Discloses it's self-reported data."""
    c = _conn()
    try:
        row = c.execute("SELECT claimed_sr,ppy,label,owner,state,opened_at FROM commitments WHERE cid=?",
                        (commitment_id,)).fetchone()
    finally:
        c.close()
    if not row:
        return {"error": "unknown commitment_id"}
    claimed_sr, ppy, label, row_owner, state_json, opened_at = row
    if not _owned(row_owner, owner):
        return {"error": "not your commitment"}
    v = _verdict(commitment_id, claimed_sr, ppy, label, json.loads(state_json))
    record = {"kind": "track_record", "survives": v.get("survives"),
              "verdict": v.get("verdict", v.get("verdict_label")),
              "commitment_id": commitment_id, "claimed_sharpe": claimed_sr,
              "realized_sharpe": v.get("realized_sharpe"), "observations": v.get("observations"),
              "verdict_label": v.get("verdict_label"), "opened_at": opened_at, "label": label,
              "data_source": "self_reported"}    # honest: reported returns, not a verified performance feed
    return _rcpt.issue_receipt(record, private_hex, public_hex)


def _selftest():
    import tempfile, random
    global _STORE
    _STORE = Path(tempfile.mkdtemp()) / "commitments.db"
    _INIT.clear()
    rng = random.Random(0)
    o = open_commitment(0.2, label="test", owner=owner_hash("a"))
    cid = o["commitment_id"]
    for _ in range(15):
        report(cid, [rng.gauss(0.0, 0.012) for _ in range(50)], owner=owner_hash("a"))
    st = status(cid, owner=owner_hash("a"))
    assert st["observations"] == 750 and st["verdict_label"] in ("BROKEN", "DECAYED")
    assert "error" in report(cid, [0.1], owner=owner_hash("b"))     # owner-bound
    print(f"commitments selftest: OK (SQLite, {st['observations']} obs, verdict={st['verdict_label']}, owner-bound)")


if __name__ == "__main__":
    _selftest()
