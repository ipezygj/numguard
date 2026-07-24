"""commitments — hold a backtest promise over time, at O(1) per observation.

An agent OPENS a commitment (its claimed per-period Sharpe), then REPORTS live returns as they arrive. numguard
folds each return into running sufficient statistics (Welford; it NEVER stores the raw returns) and can judge
HELD / DECAYED / BROKEN at any time from ~5 floats. So the load is the answer to "won't tracking every customer
forever crush the box?": there is NO background compute (updates happen only when an agent pushes data), and
each commitment is constant memory regardless of how long it runs. Tracking N customers for years costs
O(observations pushed), not O(time × customers).

Hardened like the credit ledger: one lock around read-modify-write, atomic file replace, and a hard cap on the
number of commitments (oldest evicted) so the store can't grow without bound.
"""
from __future__ import annotations
import hashlib, hmac, json, os, secrets, threading, time
from pathlib import Path

from . import forward as _forward, receipt as _rcpt, _limits


def owner_hash(api_key: str) -> str:
    """A non-reversible owner tag for a commitment — so a leaked commitment_id alone can't poison or read a
    track record; you also need the api_key that opened it. We store the hash, never the key."""
    return hashlib.sha256(("numguard-owner:" + str(api_key)).encode()).hexdigest()[:32]

MAX_COMMITMENTS = 50_000
_STORE = Path(os.environ.get("NUMGUARD_COMMITMENTS", Path.home() / ".numguard" / "commitments.json"))
_LOCK = threading.Lock()


def _read(for_write: bool = False) -> dict:
    try:
        return json.loads(_STORE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        if for_write:
            raise RuntimeError("commitments store unreadable; refusing to overwrite")
        return {}


def _save(d: dict) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_name(f"{_STORE.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, _STORE)


def _evict(d: dict) -> None:
    if len(d) > MAX_COMMITMENTS:
        for cid in sorted(d, key=lambda k: d[k].get("updated_at", 0))[:len(d) - MAX_COMMITMENTS]:
            d.pop(cid, None)


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


def _verdict(cid: str, c: dict) -> dict:
    n, mean, sd, sk, ku = _forward.stream_stats(c["state"])
    if n < 8:
        return {"commitment_id": cid, "observations": n, "claimed_sharpe": c["claimed_sr"],
                "verdict_label": "PENDING", "survives": None,
                "verdict": f"PENDING — {n} live observations so far; need >= 8 to judge. Keep reporting."}
    v = _forward.reconcile_from_stats(c["claimed_sr"], n, mean, sd, sk, ku, periods_per_year=c.get("ppy", 252))
    v["commitment_id"] = cid
    v["observations"] = n
    if c.get("label"):
        v["label"] = c["label"]
    return v


def _owned(c: dict, owner: str) -> bool:
    """A commitment with no owner is open; otherwise the caller's owner tag must match (constant-time)."""
    o = c.get("owner", "")
    return (not o) or hmac.compare_digest(o, owner or "")


def open_commitment(claimed_sr: float, periods_per_year: int = 252, label: str = "", owner: str = "") -> dict:
    """Open a commitment to a claimed per-period Sharpe. Returns a commitment_id to report live returns against.
    `owner` (a hash of the caller's api_key) binds it so only the opener can report/read it."""
    if claimed_sr is None or float(claimed_sr) <= 0:
        raise ValueError("claimed_sr must be a positive per-period Sharpe")
    cid = "cm_" + secrets.token_urlsafe(12)
    now = int(time.time())
    with _LOCK:
        d = _read(for_write=True)
        d[cid] = {"claimed_sr": float(claimed_sr), "ppy": int(periods_per_year), "label": str(label)[:80],
                  "state": _forward.stream_init(), "owner": owner, "opened_at": now, "updated_at": now}
        _evict(d)
        _save(d)
    return {"commitment_id": cid, "claimed_sharpe": float(claimed_sr), "observations": 0,
            "note": "report live returns to this id with report_returns; numguard folds them at O(1) each."}


def report(commitment_id: str, new_returns, owner: str = "") -> dict:
    """Fold new live returns into the commitment (O(1) each; raw returns are not stored) and return the current
    HELD/DECAYED/BROKEN verdict. Requires the owner that opened it."""
    xs = _clean_returns(new_returns)
    with _LOCK:
        d = _read(for_write=True)
        c = d.get(commitment_id)
        if not c:
            return {"error": "unknown commitment_id"}
        if not _owned(c, owner):
            return {"error": "not your commitment"}
        for x in xs:
            _forward.stream_update(c["state"], x)
        c["updated_at"] = int(time.time())
        _save(d)
        return _verdict(commitment_id, c)


def status(commitment_id: str, owner: str = "") -> dict:
    """The current verdict for a commitment without adding data. Requires the owner that opened it."""
    c = _read().get(commitment_id)
    if not c:
        return {"error": "unknown commitment_id"}
    if not _owned(c, owner):
        return {"error": "not your commitment"}
    return _verdict(commitment_id, c)


def signed_track_record(commitment_id: str, private_hex: str, public_hex: str = "", owner: str = "") -> dict:
    """Issue a portable, signed (Ed25519) attestation of a commitment's live track record — the accountable
    credential an agent can SHOW ('numguard-verified: 600 live obs, edge HELD'). Recomputed server-side from the
    running statistics; tamper-evident; verifiable by anyone with the public key."""
    c = _read().get(commitment_id)
    if not c:
        return {"error": "unknown commitment_id"}
    if not _owned(c, owner):
        return {"error": "not your commitment"}
    v = _verdict(commitment_id, c)
    record = {"kind": "track_record", "survives": v.get("survives"),
              "verdict": v.get("verdict", v.get("verdict_label")),
              "commitment_id": commitment_id, "claimed_sharpe": c["claimed_sr"],
              "realized_sharpe": v.get("realized_sharpe"), "observations": v.get("observations"),
              "verdict_label": v.get("verdict_label"), "opened_at": c.get("opened_at"),
              "label": c.get("label", "")}
    return _rcpt.issue_receipt(record, private_hex, public_hex)


def _selftest():
    import tempfile, random
    global _STORE
    _STORE = Path(tempfile.mkdtemp()) / "commitments.json"
    rng = random.Random(0)
    o = open_commitment(0.2, label="test")
    cid = o["commitment_id"]
    # report a dead edge in chunks -> eventually BROKEN, constant state
    for _ in range(15):
        report(cid, [rng.gauss(0.0, 0.012) for _ in range(50)])
    st = status(cid)
    assert st["observations"] == 750 and st["verdict_label"] in ("BROKEN", "DECAYED")
    assert len(_read()[cid]["state"]) == 5, "state must stay ~5 floats regardless of history"
    print(f"commitments selftest: OK ({st['observations']} obs, verdict={st['verdict_label']}, "
          f"state={len(_read()[cid]['state'])} floats)")


if __name__ == "__main__":
    _selftest()
