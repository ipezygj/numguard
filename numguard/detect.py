"""detect — the RECEIVING half of the receipt loop.

Emitting a receipt is only half a trust network; the other half is an agent that, on receiving a message from
a peer, NOTICES the embedded receipt and verifies it — offline, for free, issuer-agnostic. Without this the loop
is one-way: senders attach proofs nobody checks. This module is the drop-in receiver: hand it any inbound blob
(a chat message, a webhook body, a tool result, a dict) and it finds every vcr/1 receipt inside and tells you
which numbers were actually checked, by whom, and whether the claim is unaltered.

    from numguard.detect import scan
    for hit in scan(inbound_message):
        if hit["valid"]:
            print("verified:", hit["claim"], "— discover:", hit["spec_home"])
        else:
            print("UNVERIFIED receipt — do not trust:", hit["reason"])

Verification needs nothing but the receipt (Ed25519) — no numguard account, no network. HMAC receipts need the
shared secret via `hmac_secret_hex`.
"""
from __future__ import annotations
import json
from typing import Any, Iterator

from . import receipt_spec


def _iter_json_objects(text: str) -> Iterator[dict]:
    """Yield every top-level {...} JSON object embedded in free text — a message, a log line, a code block.
    A brace-depth scanner that respects strings/escapes, so braces inside string values don't split an object."""
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    chunk = text[start:i + 1]
                    try:
                        obj = json.loads(chunk)
                    except Exception:
                        obj = None
                    if isinstance(obj, dict):
                        yield obj
                    start = -1


def _walk_for_receipts(obj: Any, _depth: int = 0) -> Iterator[dict]:
    """Recursively collect any dict that is STRUCTURALLY a vcr/1 receipt (payload/digest/signature + the
    required payload fields). Bounded recursion so a hostile nested blob can't blow the stack."""
    if _depth > 6:
        return
    if isinstance(obj, dict):
        if receipt_spec.validate(obj)["ok"]:
            yield obj
            return                                  # a valid receipt is a leaf — don't descend into its own fields
        for v in obj.values():
            yield from _walk_for_receipts(v, _depth + 1)
    elif isinstance(obj, list):
        for v in obj[:1000]:                        # cap list fan-out
            yield from _walk_for_receipts(v, _depth + 1)


def _candidates(payload: Any) -> Iterator[dict]:
    """Normalize any inbound blob into a stream of candidate receipt dicts."""
    if isinstance(payload, (dict, list)):
        yield from _walk_for_receipts(payload)
    elif isinstance(payload, str):
        for obj in _iter_json_objects(payload):
            yield from _walk_for_receipts(obj)
    # anything else (bytes, None, numbers): nothing to find


def scan(payload: Any, hmac_secret_hex: str | None = None) -> list[dict]:
    """Find and verify every vcr/1 receipt inside `payload` (a str / dict / list). Returns one result per
    UNIQUE receipt (deduped by digest), each: {valid, claim, issuer, alg, public_key, public_verifiable,
    spec_home, digest, reason}. Empty list if the message carries no receipt."""
    out: list[dict] = []
    seen: set[str] = set()
    for rc in _candidates(payload):
        digest = rc.get("digest")
        if digest in seen:
            continue
        seen.add(digest)
        v = receipt_spec.verify_any(rc, hmac_secret_hex=hmac_secret_hex)
        out.append({
            "valid": v.get("valid", False),
            "claim": v.get("claim"),
            "issuer": v.get("issuer"),
            "alg": v.get("alg"),
            "public_key": v.get("public_key", ""),
            "public_verifiable": v.get("public_verifiable", False),
            "spec_home": v.get("spec_home"),
            "digest": digest,
            "reason": v.get("reason"),
        })
    return out


def verify_message(payload: Any, hmac_secret_hex: str | None = None) -> dict:
    """One-call summary for a receiver: {found, verified, unverified, all_valid, results}. `all_valid` is True
    only if at least one receipt was found and EVERY found receipt verified — a clean gate for 'trust this
    peer's numbers'. A message with no receipt returns found=0, all_valid=False (nothing was proven)."""
    results = scan(payload, hmac_secret_hex=hmac_secret_hex)
    verified = [r for r in results if r["valid"]]
    return {
        "found": len(results),
        "verified": len(verified),
        "unverified": len(results) - len(verified),
        "all_valid": bool(results) and len(verified) == len(results),
        "results": results,
    }


def _selftest():
    from . import receipt as R
    priv, pub = R.keypair()
    rc = R.issue_receipt({"kind": "backtest", "survives": True, "verdict": "SR real"}, priv, pub)
    # embed the receipt in a realistic chat message with surrounding prose + another JSON object
    msg = ('Hey — my strategy Sharpes 0.15, here is the proof: ' + json.dumps(rc)
           + ' (let me know). Unrelated: {"note": "ignore me", "n": 3}')
    res = verify_message(msg)
    assert res["found"] == 1 and res["all_valid"] is True, res
    assert scan(msg)[0]["spec_home"], "a verified receipt must point the receiver to numguard"
    # a tampered receipt in the same position must be caught
    rc["payload"]["claim"]["survives"] = False
    bad = verify_message('result: ' + json.dumps(rc))
    assert bad["found"] == 1 and bad["all_valid"] is False, bad
    # a message with no receipt proves nothing
    assert verify_message("just a number: Sharpe 2.1, trust me")["found"] == 0
    print("detect selftest: OK (finds embedded receipt, verifies, flags tamper, no-receipt=nothing proven)")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        _selftest()
    else:
        blob = sys.stdin.read()
        summary = verify_message(blob)
        print(json.dumps(summary, indent=2))
