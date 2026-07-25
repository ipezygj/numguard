"""precommit — tamper-evident FORWARD pre-registration of a strategy claim ("clinicaltrials.gov for backtests").

The genuinely novel step over `commitments`: attack the most valuable fraud vector — a *backfilled or
cherry-registered* live track record. Two properties make it real (not theater):

  1. COMMIT-BEFORE-OUTCOME. Opening a pre-commitment produces a canonical DIGEST of the claim (strategy id,
     claimed Sharpe, forward horizon, reporting schedule, created_at), SIGNED (Ed25519). Anchor that digest on
     Base and its existence at created_at becomes operator-uncontrollable — you can prove the claim predated the
     outcome, so it can't be a curve-fit chosen after the fact.
  2. TAMPER-EVIDENT TIMELINE. Every report is appended to a HASH-CHAINED log (entry_hash = SHA256(prev | seq |
     ts | returns_digest)) with monotonic timestamps. Editing, reordering, backdating, or deleting any past
     report breaks every later hash → `verify_chain` detects it. A free, public audit anyone can run.

HONEST LIMIT (kept explicit, per the project's no-unearned-evidence rule): this makes the CLAIM and the
TIMELINE tamper-evident. It does NOT by itself prove the reported return VALUES are real — that needs an
operator-uncontrollable feed (exchange/broker-signed, or an on-chain oracle), which is the separate, heavier
"verifiable execution" direction. So every artifact carries data_source: "self_reported", and the strong,
earned claim is precisely: "committed at T before the outcome, and the report timeline was never rewritten."
The registration digest is only fully non-repudiable once ANCHORED on-chain (numguard signing alone proves
numguard saw it at T; the chain anchor proves it to everyone).
"""
from __future__ import annotations
import hashlib, hmac, json, os, secrets, sqlite3, threading, time
from pathlib import Path

from . import forward as _forward, receipt as _rcpt, _limits

MAX_PRECOMMITS = 50_000
_STORE = Path(os.environ.get("NUMGUARD_PRECOMMITS", Path.home() / ".numguard" / "precommit.db"))
_LOCK = threading.Lock()
_INIT: set = set()


def owner_hash(api_key: str) -> str:
    return hashlib.sha256(("numguard-owner:" + str(api_key)).encode()).hexdigest()[:32]


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _conn() -> sqlite3.Connection:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_STORE), timeout=10)
    if str(_STORE) not in _INIT:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("""CREATE TABLE IF NOT EXISTS precommit (
            pid TEXT PRIMARY KEY, strategy_id TEXT, claimed_sr REAL, ppy INTEGER,
            horizon INTEGER, schedule TEXT, owner TEXT, digest TEXT, sig TEXT, pubkey TEXT,
            state TEXT, head_hash TEXT, seq INTEGER, created_at INTEGER, updated_at INTEGER, anchor_tx TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS preport (
            pid TEXT, seq INTEGER, ts INTEGER, prev_hash TEXT, entry_hash TEXT, n INTEGER, returns_digest TEXT,
            PRIMARY KEY (pid, seq))""")
        c.commit()
        _INIT.add(str(_STORE))
    return c


def _clean_returns(xs, cap: int = _limits.MAX_ITEMS):
    if not isinstance(xs, (list, tuple)) or not xs or len(xs) > cap:
        raise ValueError(f"new_returns must be a non-empty list of <= {cap} numbers")
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


GENESIS = "0" * 64


def open_precommitment(strategy_id: str, claimed_sr: float, horizon_periods: int,
                       private_hex: str = "", public_hex: str = "", periods_per_year: int = 252,
                       schedule: str = "", owner: str = "") -> dict:
    """Pre-register a forward claim BEFORE outcomes. Returns a signed, immutable registration digest (+ receipt)
    you should ANCHOR on-chain to make 'committed at T' operator-uncontrollable."""
    if claimed_sr is None or float(claimed_sr) <= 0:
        raise ValueError("claimed_sr must be a positive per-period Sharpe")
    if not isinstance(horizon_periods, int) or horizon_periods <= 0:
        raise ValueError("horizon_periods must be a positive integer (the forward window you commit to)")
    pid = "pc_" + secrets.token_urlsafe(12)
    now = int(time.time())
    registration = {
        "kind": "precommitment", "pid": pid, "strategy_id": str(strategy_id)[:120],
        "claimed_sharpe": float(claimed_sr), "periods_per_year": int(periods_per_year),
        "horizon_periods": int(horizon_periods), "schedule": str(schedule)[:80], "created_at": now,
    }
    digest = _sha(registration)
    receipt = _rcpt.issue_receipt({**registration, "digest": digest, "data_source": "self_reported"},
                                  private_hex, public_hex)
    sig = receipt.get("signature", "")
    pub = receipt.get("public_key", public_hex)
    with _LOCK:
        c = _conn()
        try:
            c.execute("INSERT INTO precommit VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (pid, registration["strategy_id"], float(claimed_sr), int(periods_per_year),
                       int(horizon_periods), registration["schedule"], owner, digest, sig, pub,
                       json.dumps(_forward.stream_init()), GENESIS, 0, now, now, None))
            c.execute("""DELETE FROM precommit WHERE pid IN (
                SELECT pid FROM precommit ORDER BY updated_at DESC LIMIT -1 OFFSET ?)""", (MAX_PRECOMMITS,))
            c.commit()
        finally:
            c.close()
    return {"pid": pid, "registration": registration, "digest": digest,
            "signature": sig, "public_key": pub, "receipt": receipt,
            "anchor_hint": "anchor this digest on-chain (anchor_receipt) so 'committed at created_at' is provable "
                           "to everyone, not just to numguard.",
            "data_source": "self_reported"}


def report(pid: str, new_returns, owner: str = "") -> dict:
    """Append a report to the tamper-evident chain (hash-chained, monotonic ts) and fold returns for the verdict.
    Raw returns are not stored — only their digest, for the chain."""
    xs = _clean_returns(new_returns)
    rdigest = _sha(xs)
    now = int(time.time())
    with _LOCK:
        c = _conn()
        try:
            row = c.execute("""SELECT claimed_sr,ppy,strategy_id,owner,state,head_hash,seq,updated_at
                               FROM precommit WHERE pid=?""", (pid,)).fetchone()
            if not row:
                return {"error": "unknown pid"}
            claimed_sr, ppy, strategy_id, row_owner, state_json, head, seq, last_ts = row
            if not _owned(row_owner, owner):
                return {"error": "not your precommitment"}
            ts = now if now > last_ts else last_ts + 1  # enforce strictly monotonic timeline (no backdating)
            new_seq = seq + 1
            entry_hash = hashlib.sha256(f"{head}|{new_seq}|{ts}|{rdigest}".encode()).hexdigest()
            state = json.loads(state_json)
            for x in xs:
                _forward.stream_update(state, x)
            c.execute("INSERT INTO preport VALUES (?,?,?,?,?,?,?)",
                      (pid, new_seq, ts, head, entry_hash, len(xs), rdigest))
            c.execute("UPDATE precommit SET state=?, head_hash=?, seq=?, updated_at=? WHERE pid=?",
                      (json.dumps(state), entry_hash, new_seq, ts, pid))
            c.commit()
        finally:
            c.close()
    v = _verdict(pid, claimed_sr, ppy, strategy_id, state)
    v.update({"seq": new_seq, "head_hash": entry_hash, "reported_at": ts})
    return v


def _verdict(pid: str, claimed_sr: float, ppy: int, strategy_id: str, state: dict) -> dict:
    n, mean, sd, sk, ku = _forward.stream_stats(state)
    if n < 8:
        return {"pid": pid, "observations": n, "claimed_sharpe": claimed_sr, "strategy_id": strategy_id,
                "verdict_label": "PENDING", "survives": None, "data_source": "self_reported",
                "verdict": f"PENDING — {n} live observations; need >= 8 to judge."}
    v = _forward.reconcile_from_stats(claimed_sr, n, mean, sd, sk, ku, periods_per_year=ppy)
    v.update({"pid": pid, "observations": n, "strategy_id": strategy_id, "data_source": "self_reported"})
    return v


def verify_chain(pid: str) -> dict:
    """FREE, public: recompute the report hash-chain from genesis and check timestamps are monotonic. Detects any
    edit / reorder / backdate / deletion of a past report. Returns ok + the audited head anyone can pin."""
    c = _conn()
    try:
        pc = c.execute("SELECT digest,created_at,head_hash,seq FROM precommit WHERE pid=?", (pid,)).fetchone()
        if not pc:
            return {"error": "unknown pid"}
        rows = c.execute("SELECT seq,ts,prev_hash,entry_hash,returns_digest FROM preport WHERE pid=? ORDER BY seq",
                         (pid,)).fetchall()
    finally:
        c.close()
    digest, created_at, stored_head, stored_seq = pc
    prev, last_ts = GENESIS, created_at
    for i, (seq, ts, prev_hash, entry_hash, rdigest) in enumerate(rows, start=1):
        if seq != i or prev_hash != prev:
            return {"pid": pid, "ok": False, "bad_seq": seq, "reason": "chain link broken"}
        if ts < last_ts:
            return {"pid": pid, "ok": False, "bad_seq": seq, "reason": "non-monotonic timestamp (backdated)"}
        recomputed = hashlib.sha256(f"{prev}|{seq}|{ts}|{rdigest}".encode()).hexdigest()
        if recomputed != entry_hash:
            return {"pid": pid, "ok": False, "bad_seq": seq, "reason": "entry hash mismatch (tampered)"}
        prev, last_ts = entry_hash, ts
    if prev != stored_head or len(rows) != stored_seq:
        return {"pid": pid, "ok": False, "reason": "head does not match report log"}
    return {"pid": pid, "ok": True, "reports": len(rows), "precommit_digest": digest,
            "created_at": created_at, "head_hash": prev,
            "note": "timeline is tamper-evident; claim was committed at created_at (anchor the digest on-chain "
                    "to make that provable to everyone). data_source: self_reported."}


def get_precommit(pid: str) -> dict:
    """FREE, public: the registration entry (the immutable pre-commitment) + current head. The 'registry entry'."""
    c = _conn()
    try:
        row = c.execute("""SELECT strategy_id,claimed_sr,ppy,horizon,schedule,digest,sig,pubkey,created_at,
                           head_hash,seq,anchor_tx FROM precommit WHERE pid=?""", (pid,)).fetchone()
    finally:
        c.close()
    if not row:
        return {"error": "unknown pid"}
    (sid, sr, ppy, horizon, sched, digest, sig, pub, created, head, seq, anchor) = row
    return {"pid": pid, "strategy_id": sid, "claimed_sharpe": sr, "periods_per_year": ppy,
            "horizon_periods": horizon, "schedule": sched, "digest": digest, "signature": sig,
            "public_key": pub, "created_at": created, "reports": seq, "head_hash": head,
            "anchor_tx": anchor, "data_source": "self_reported"}


def set_anchor(pid: str, tx: str, owner: str = "") -> dict:
    """Record the on-chain anchor tx for the registration digest (makes 'committed at T' publicly provable)."""
    with _LOCK:
        c = _conn()
        try:
            row = c.execute("SELECT owner FROM precommit WHERE pid=?", (pid,)).fetchone()
            if not row:
                return {"error": "unknown pid"}
            if not _owned(row[0], owner):
                return {"error": "not your precommitment"}
            c.execute("UPDATE precommit SET anchor_tx=? WHERE pid=?", (str(tx)[:80], pid))
            c.commit()
        finally:
            c.close()
    return {"pid": pid, "anchor_tx": str(tx)[:80]}


def _selftest():
    import tempfile, random
    global _STORE
    _STORE = Path(tempfile.mkdtemp()) / "precommit.db"
    _INIT.clear()
    rng = random.Random(0)
    o = open_precommitment("rsi_v1", 0.2, horizon_periods=250, owner=owner_hash("a"))
    pid = o["pid"]
    assert o["digest"] and len(o["digest"]) == 64
    d0 = o["digest"]
    for _ in range(12):
        report(pid, [rng.gauss(0.0, 0.012) for _ in range(20)], owner=owner_hash("a"))
    chk = verify_chain(pid)
    assert chk["ok"] and chk["reports"] == 12, chk
    # registration digest stable after reporting (claim can't be silently changed)
    assert get_precommit(pid)["digest"] == d0
    # owner-bound
    assert "error" in report(pid, [0.1], owner=owner_hash("b"))
    # tamper detection: corrupt one stored entry_hash → chain must fail at that seq
    c = _conn()
    c.execute("UPDATE preport SET entry_hash=? WHERE pid=? AND seq=5", ("deadbeef" * 8, pid))
    c.commit(); c.close()
    bad = verify_chain(pid)
    assert not bad["ok"] and bad["bad_seq"] == 5, bad
    print(f"precommit selftest: OK (pre-registration signed+stable, {chk['reports']} chained reports, "
          f"tamper at seq 5 detected, owner-bound)")


if __name__ == "__main__":
    _selftest()
