"""transparency — an append-only, hash-chained public LEDGER of the verdicts numguard has issued.

A single signed receipt proves ONE verdict is genuine. That is not yet a reputation: a dishonest issuer
could sign a verdict, show it, then quietly drop it — or backdate/rewrite its history. This ledger closes
that gap the way Certificate Transparency does for TLS certs: every published verdict is appended to a
hash chain, where each entry commits to the one before it

    entry.hash = SHA256( prev.hash | seq | ts | receipt.digest )

so changing, reordering, or deleting ANY past entry changes every hash after it — a tamper an agent detects
by recomputing the chain (`verify_log`). The head hash is a single fingerprint of the WHOLE history; sign it
(`signed_head`) and an agent can pin it and later prove numguard never rewrote what it once attested.

What an agent gets that it couldn't before:
  • `/receipts`      — browse the track record; every entry is a full receipt it verifies with the pubkey
  • `/log/head`      — a signed fingerprint of the entire history to pin
  • `/log/verify`    — recompute the chain and confirm nothing was rewritten
That turns "numguard signed a verdict" into "any agent can independently audit numguard's whole record."

Store: JSONL at ~/.numguard/transparency.jsonl (env NUMGUARD_TRANSPARENCY). Single-writer (the hosted
service is one process); appends are serialized under a lock.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

from . import identity

_STORE = Path(os.environ.get("NUMGUARD_TRANSPARENCY", Path.home() / ".numguard" / "transparency.jsonl"))
_LOCK = threading.Lock()

GENESIS = "0" * 64   # prev-hash of the first entry


def _entry_hash(prev: str, seq: int, ts: int, digest: str) -> str:
    return hashlib.sha256(f"{prev}|{seq}|{ts}|{digest}".encode()).hexdigest()


# ------------------------------------------------------------------ durable backend (Turso, optional)
# A free-tier host has no persistent disk → the JSONL ledger is wiped on every redeploy. Set
# NUMGUARD_TURSO_URL (libsql://… or https://…) + NUMGUARD_TURSO_TOKEN and the ledger lives in a durable
# libSQL/Turso DB instead — the public track record then SURVIVES redeploys with no re-seeding. Unset =>
# the local JSONL file (unchanged; what the tests exercise). Stdlib-only HTTP, no new dependency.
import urllib.request as _urllib
import urllib.error as _urllib_error

# Tolerate ANY messy dashboard paste (a phone-typed config): scan the COMBINED content of both env vars
# and extract the URL and the JWT by shape — so it works even if the URL and token got pasted into the
# same field, swapped, or wrapped across lines. A URL is libsql://…/https://… up to whitespace; a Turso
# token is a 3-part JWT (starts `eyJ`). Runs once at import on env values — not on request input.
import re as _re

_raw_cfg = os.environ.get("NUMGUARD_TURSO_URL", "") + " " + os.environ.get("NUMGUARD_TURSO_TOKEN", "")
_url_m = _re.search(r'(?:libsql|https|http)://[^\s]+', _raw_cfg)
_jwt_m = _re.search(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', _raw_cfg)
_TURSO_URL = _url_m.group(0) if _url_m else ""
_TURSO_TOKEN = _jwt_m.group(0) if _jwt_m else ""
_DDL = ("CREATE TABLE IF NOT EXISTS numguard_ledger (seq INTEGER PRIMARY KEY, ts INTEGER, digest TEXT, kind TEXT, "
        "survives TEXT, public_key TEXT, prev TEXT, hash TEXT, receipt TEXT)")
_COLS = ("seq", "ts", "digest", "kind", "survives", "public_key", "prev", "hash", "receipt")
_table_ready = [False]


def _turso_on() -> bool:
    return bool(_TURSO_URL and _TURSO_TOKEN)


def config_status() -> dict:
    """Safe diagnostic (no secret values): what the server actually resolved for the ledger backend.
    Lets an operator see whether the durable Turso config took effect without exposing the token."""
    raw = os.environ.get("NUMGUARD_TURSO_URL", "") + " " + os.environ.get("NUMGUARD_TURSO_TOKEN", "")
    return {"backend": "turso" if _turso_on() else "file (ephemeral)",
            "durable": _turso_on(),
            "url_resolved": _http_base() if _TURSO_URL else "",
            "url_env_len": len(os.environ.get("NUMGUARD_TURSO_URL", "")),
            "token_env_len": len(os.environ.get("NUMGUARD_TURSO_TOKEN", "")),
            "url_extracted": bool(_TURSO_URL),
            "token_extracted": bool(_TURSO_TOKEN),
            # structural hints (no secret value) to diagnose why the JWT wasn't found:
            "has_eyJ": "eyJ" in raw, "has_authtoken": "authtoken" in raw.lower(),
            "has_pct": "%" in raw, "dots_in_raw": raw.count("."), "spaces_in_raw": raw.count(" ") - 1,
            "raw_tail6": raw.rstrip()[-6:]}


def _http_base() -> str:
    u = _TURSO_URL.split("?")[0]                 # drop any ?authToken=… query — token goes in the Bearer header
    if u.startswith("libsql://"):
        u = "https://" + u[len("libsql://"):]
    return u.rstrip("/")


def _arg(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, int) and not isinstance(v, bool):
        return {"type": "integer", "value": str(v)}
    return {"type": "text", "value": str(v)}


def _turso(sql: str, args=(), _ensure: bool = True):
    """Run one SQL statement on Turso via the HTTP pipeline API; return (rows_as_dicts, affected)."""
    reqs = []
    if _ensure and not _table_ready[0]:
        reqs.append({"type": "execute", "stmt": {"sql": _DDL}})
    reqs.append({"type": "execute", "stmt": {"sql": sql, "args": [_arg(a) for a in args]}})
    reqs.append({"type": "close"})
    body = json.dumps({"requests": reqs}).encode()
    req = _urllib.Request(_http_base() + "/v2/pipeline", data=body,
                          headers={"Authorization": f"Bearer {_TURSO_TOKEN}", "Content-Type": "application/json"})
    try:
        with _urllib.urlopen(req, timeout=15) as r:
            data = json.load(r)
    except _urllib_error.HTTPError as e:                    # surface Turso's actual complaint, not a bare code
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"turso HTTP {e.code} @ {_http_base()}: {detail}") from None
    _table_ready[0] = True
    execs = [x for x in data.get("results", []) if x.get("type") == "ok"
             and x.get("response", {}).get("type") == "execute"]
    if not execs:
        raise RuntimeError(f"turso: {data}")
    res = execs[-1]["response"]["result"]              # our statement is the last execute
    cols = [c.get("name") for c in res.get("cols", [])]
    rows = []
    for row in res.get("rows", []):
        rows.append({cols[i]: (cell.get("value") if cell.get("type") != "null" else None)
                     for i, cell in enumerate(row)})
    return rows, res.get("affected_row_count", 0)


def _row_to_entry(row: dict) -> dict:
    sv = row.get("survives")
    return {"seq": int(row["seq"]), "ts": int(row["ts"]), "digest": row["digest"], "kind": row["kind"],
            "survives": (True if sv == "true" else False if sv == "false" else None),
            "public_key": row.get("public_key") or "", "prev": row["prev"], "hash": row["hash"],
            "receipt": json.loads(row["receipt"]) if row.get("receipt") else None}


def _iter_lines():
    if _turso_on():
        rows, _ = _turso("SELECT seq,ts,digest,kind,survives,public_key,prev,hash,receipt FROM numguard_ledger ORDER BY seq ASC")
        for row in rows:
            yield _row_to_entry(row)
        return
    if not _STORE.exists():
        return
    with _STORE.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _last() -> dict | None:
    if _turso_on():
        rows, _ = _turso("SELECT seq,ts,digest,kind,survives,public_key,prev,hash,receipt FROM numguard_ledger "
                         "ORDER BY seq DESC LIMIT 1")
        return _row_to_entry(rows[0]) if rows else None
    last = None
    for e in _iter_lines():
        last = e
    return last


def publish(receipt: dict, ts: int | None = None) -> dict:
    """Append a receipt to the public ledger; returns the log entry (with its chain hash + seq).

    `receipt` is what receipt.issue_receipt returned: {payload, digest, public_key, signature}. The chain
    binds the receipt's `digest` (which itself covers the exact claim + verdict), so the ledger proves both
    membership and order without re-storing anything the digest doesn't already commit to."""
    digest = receipt.get("digest") or hashlib.sha256(
        json.dumps(receipt.get("payload", {}), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    payload = receipt.get("payload") or {}
    claim = payload.get("claim") if isinstance(payload.get("claim"), dict) else {}
    kind = claim.get("kind") or payload.get("kind") or payload.get("tool") or "claim"
    survives = claim.get("survives", payload.get("survives"))
    with _LOCK:
        prev = _last()
        seq = 0 if prev is None else prev["seq"] + 1
        prev_hash = GENESIS if prev is None else prev["hash"]
        ts = int(ts if ts is not None else time.time())
        h = _entry_hash(prev_hash, seq, ts, digest)
        entry = {
            "seq": seq, "ts": ts, "digest": digest, "kind": kind, "survives": survives,
            "public_key": receipt.get("public_key", ""), "prev": prev_hash, "hash": h,
            "receipt": receipt,          # full signed receipt, so /receipts serves verifiable proof
        }
        if _turso_on():
            sv = "true" if survives is True else "false" if survives is False else None
            _turso("INSERT INTO numguard_ledger (seq,ts,digest,kind,survives,public_key,prev,hash,receipt) "
                   "VALUES (?,?,?,?,?,?,?,?,?)",
                   (seq, ts, digest, kind, sv, entry["public_key"], prev_hash, h,
                    json.dumps(receipt, separators=(",", ":"))))
            return entry
        _STORE.parent.mkdir(parents=True, exist_ok=True)
        with _STORE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return entry


def head() -> dict:
    """The current tip: {count, seq, head_hash}. head_hash is a fingerprint of the ENTIRE history."""
    last = _last()
    if last is None:
        return {"count": 0, "seq": -1, "head_hash": GENESIS}
    return {"count": last["seq"] + 1, "seq": last["seq"], "head_hash": last["hash"]}


def signed_head() -> dict:
    """The head, signed with the issuer key — an agent pins this and can later prove no history rewrite.
    `receipt` is a full, self-contained proof: verify it with numguard.receipt.verify_receipt (or the
    published pubkey) and read the committed head from its payload."""
    from . import receipt as _rcpt
    priv, pub = identity.issuer()
    h = head()
    sig_receipt = _rcpt.issue_receipt({"log_head": h, "issuer": identity.ISSUER_NAME}, priv, pub)
    return {"head": h, "public_key": pub, "key_id": identity.key_id(pub), "receipt": sig_receipt}


def entries(offset: int = 0, limit: int = 50) -> list[dict]:
    """A page of the ledger, newest-first. Each item carries its full verifiable receipt."""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    alle = list(_iter_lines())
    alle.reverse()
    return alle[offset:offset + limit]


def get(digest: str) -> dict | None:
    for e in _iter_lines():
        if e.get("digest") == digest:
            return e
    return None


def verify_log() -> dict:
    """Recompute the whole chain from genesis. {ok, count, head_hash, bad_seq}. bad_seq is the first entry
    whose stored hash disagrees with the recomputation (evidence of a rewrite), or None."""
    prev = GENESIS
    count = 0
    for i, e in enumerate(_iter_lines()):
        if e.get("seq") != i or e.get("prev") != prev:
            return {"ok": False, "count": count, "head_hash": prev, "bad_seq": i}
        recomputed = _entry_hash(prev, e["seq"], e["ts"], e["digest"])
        if recomputed != e.get("hash"):
            return {"ok": False, "count": count, "head_hash": prev, "bad_seq": i}
        prev = e["hash"]
        count += 1
    return {"ok": True, "count": count, "head_hash": prev, "bad_seq": None}


# ------------------------------------------------------------------ selftest
def _selftest():
    import tempfile
    global _STORE
    d = Path(tempfile.mkdtemp())
    _STORE = d / "t.jsonl"
    from . import receipt as _rcpt
    priv, pub = _rcpt.keypair()
    # publish three verdicts
    for i in range(3):
        r = _rcpt.issue_receipt({"claim": {"kind": "backtest", "survives": bool(i % 2)}, "n": i}, priv, pub)
        e = publish(r, ts=1000 + i)
        assert e["seq"] == i
    v = verify_log()
    assert v["ok"] and v["count"] == 3, v
    h = head()
    assert h["count"] == 3 and h["head_hash"] == v["head_hash"]
    # feed is newest-first and each receipt verifies
    feed = entries(0, 10)
    assert [e["seq"] for e in feed] == [2, 1, 0]
    assert all(_rcpt.verify_receipt(e["receipt"]) for e in feed), "a fed receipt failed verification"
    # signed head is a self-contained verifiable receipt committing to the head hash
    sh = signed_head()
    assert _rcpt.verify_receipt(sh["receipt"]) or not sh["public_key"]
    assert sh["receipt"]["payload"]["detail"]["log_head"]["head_hash"] == v["head_hash"]
    # TAMPER: rewrite a past entry's content -> chain must break
    lines = _STORE.read_text(encoding="utf-8").splitlines()
    bad = json.loads(lines[1]); bad["survives"] = (not bad["survives"]); bad["digest"] = "deadbeef" * 8
    lines[1] = json.dumps(bad, separators=(",", ":"))
    _STORE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    v2 = verify_log()
    assert (not v2["ok"]) and v2["bad_seq"] == 1, f"tamper not detected: {v2}"
    print("numguard transparency selftest: OK (chain verifies, tamper at seq 1 detected)")


if __name__ == "__main__":
    _selftest()
