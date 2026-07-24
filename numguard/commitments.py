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
import json, os, secrets, threading, time
from pathlib import Path

from . import forward as _forward, _limits

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


def open_commitment(claimed_sr: float, periods_per_year: int = 252, label: str = "") -> dict:
    """Open a commitment to a claimed per-period Sharpe. Returns a commitment_id to report live returns against."""
    if claimed_sr is None or float(claimed_sr) <= 0:
        raise ValueError("claimed_sr must be a positive per-period Sharpe")
    cid = "cm_" + secrets.token_urlsafe(12)
    now = int(time.time())
    with _LOCK:
        d = _read(for_write=True)
        d[cid] = {"claimed_sr": float(claimed_sr), "ppy": int(periods_per_year), "label": str(label)[:80],
                  "state": _forward.stream_init(), "opened_at": now, "updated_at": now}
        _evict(d)
        _save(d)
    return {"commitment_id": cid, "claimed_sharpe": float(claimed_sr), "observations": 0,
            "note": "report live returns to this id with report_returns; numguard folds them at O(1) each."}


def report(commitment_id: str, new_returns) -> dict:
    """Fold new live returns into the commitment (O(1) each; raw returns are not stored) and return the current
    HELD/DECAYED/BROKEN verdict."""
    xs = _clean_returns(new_returns)
    with _LOCK:
        d = _read(for_write=True)
        c = d.get(commitment_id)
        if not c:
            return {"error": "unknown commitment_id"}
        for x in xs:
            _forward.stream_update(c["state"], x)
        c["updated_at"] = int(time.time())
        _save(d)
        return _verdict(commitment_id, c)


def status(commitment_id: str) -> dict:
    """The current verdict for a commitment without adding data."""
    c = _read().get(commitment_id)
    if not c:
        return {"error": "unknown commitment_id"}
    return _verdict(commitment_id, c)


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
