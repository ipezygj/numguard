"""emit — the SENDER half of the receipt loop.

`detect` gave an agent the receiver reflex (find + verify a peer's receipts). `emit` is the other side: before
you POST a number, check it, and if it holds, attach a signed receipt so the claim travels with its proof. Pair
the two and the loop closes — every number an agent publishes carries a proof the next agent can verify for free.

    from numguard.emit import announce, operator_keypair
    priv, pub = operator_keypair()                       # your stable identity (generated + persisted once)
    post = announce("backtest", priv, pub,
                    "My strategy Sharpes 0.15 over 1000 periods.",
                    sr=0.15, T=1000, n_trials=1)          # -> the post text WITH an embedded, verifiable receipt
    # if the number does NOT survive its check, announce() refuses to stamp it "verified" — honesty by default.

This is the quant-operator wedge: intercept an outbound message that contains a backtested number, verify it
locally (no account, no gas), and append the receipt before publishing. A receiver running `numguard.detect`
then verifies it automatically.
"""
from __future__ import annotations
import json
import os

from . import claims, receipt as _rcpt

_RECEIPT_HEADER = "— numguard vcr/1 receipt (verify: pip install numguard) —"


class NotVerified(Exception):
    """Raised by announce() when require_survive=True and the number does not survive its check."""

    def __init__(self, verdict: dict):
        self.verdict = verdict or {}
        super().__init__(self.verdict.get("verdict") or "the number did not survive its check")


def operator_keypair() -> tuple[str, str]:
    """A STABLE issuer identity for an operator using the local library, so every receipt is consistently
    'theirs'. Resolves NUMGUARD_OPERATOR_KEY (raw 64-hex used directly, else any string is SHA-256 -> seed) ->
    file ~/.numguard/operator.json -> generate + persist. Returns (private_hex, public_hex)."""
    env = os.environ.get("NUMGUARD_OPERATOR_KEY")
    if env:
        import hashlib
        seed = bytes.fromhex(env) if len(env) == 64 and all(c in "0123456789abcdefABCDEF" for c in env) \
            else hashlib.sha256(env.encode()).digest()
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives import serialization
            sk = Ed25519PrivateKey.from_private_bytes(seed)
            priv = sk.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                    serialization.NoEncryption()).hex()
            pub = sk.public_key().public_bytes(serialization.Encoding.Raw,
                                               serialization.PublicFormat.Raw).hex()
            return priv, pub
        except Exception:
            return seed.hex(), ""                       # HMAC fallback
    path = os.path.join(os.path.expanduser("~"), ".numguard", "operator.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d["private"], d.get("public", "")
    except Exception:
        pass
    priv, pub = _rcpt.keypair()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"private": priv, "public": pub}, f)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception:
        pass                                            # ephemeral key if the disk write fails — still usable
    return priv, pub


def emit(kind: str, private_hex: str, public_hex: str = "", **inputs) -> dict:
    """Check a claim and, whatever the verdict, produce a signed receipt for it. Returns {kind, survives,
    verdict, receipt}. The receipt records the TRUE verdict — a flagged number gets an honest 'does not
    survive' receipt, not a hidden one."""
    verdict = claims.verify_claim(kind, **inputs)
    rc = _rcpt.issue_receipt(verdict, private_hex, public_hex)
    return {"kind": kind, "survives": bool(verdict.get("survives")), "verdict": verdict, "receipt": rc}


def receipt_block(receipt: dict) -> str:
    """The receipt serialized for embedding in a message — a delimited block a human can skip and
    `numguard.detect` finds automatically."""
    return f"{_RECEIPT_HEADER}\n{json.dumps(receipt)}"


def announce(kind: str, private_hex: str, public_hex: str, headline: str,
             require_survive: bool = True, **inputs) -> str:
    """Turn a numeric claim into a publishable message WITH an embedded, verifiable receipt. If the number
    survives its check, returns `headline` + a one-line verdict + the receipt block. If it does NOT survive and
    require_survive is True (default), raises `NotVerified` — the sender reflex won't let you stamp an unchecked
    number 'verified'. Set require_survive=False to publish the honest 'flagged' receipt instead."""
    e = emit(kind, private_hex, public_hex, **inputs)
    if not e["survives"] and require_survive:
        raise NotVerified(e["verdict"])
    tag = "✓ verified by numguard" if e["survives"] else "⚠ numguard: does NOT survive — posting the honest verdict"
    line = e["verdict"].get("verdict") or ("survives" if e["survives"] else "flagged")
    return f"{headline}\n\n{tag}: {line}\n\n{receipt_block(e['receipt'])}"


def _selftest():
    from . import detect
    priv, pub = _rcpt.keypair()
    # a genuine edge -> announce stamps it, and a receiver verifies it round-trip
    post = announce("backtest", priv, pub, "My strategy Sharpes 0.15 over 1000 periods.",
                    sr=0.15, T=1000, n_trials=1)
    got = detect.verify_message(post)
    assert got["found"] == 1 and got["all_valid"] is True, got
    # an overfit number -> announce refuses to call it verified
    try:
        announce("backtest", priv, pub, "best of 100 configs", sr=0.12, T=250, n_trials=100)
        raise AssertionError("announce should refuse an unverified number")
    except NotVerified:
        pass
    # ...but you can still publish the HONEST flagged receipt
    flagged = announce("backtest", priv, pub, "being honest", require_survive=False,
                       sr=0.12, T=250, n_trials=100)
    assert detect.scan(flagged)[0]["claim"]["survives"] is False
    print("emit selftest: OK (announce stamps a real edge, refuses an overfit, can post an honest flag)")


if __name__ == "__main__":
    _selftest()
