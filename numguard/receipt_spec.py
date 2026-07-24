"""receipt_spec — the open Verifiable-Claim-Receipt standard (vcr/1).

A numguard receipt isn't a numguard-private format: it's an OPEN standard any issuer can produce and ANYONE can
verify with only a public key — no numguard, no account, no network. This module is the standard's reference:
the schema, a structural validator, and an ISSUER-AGNOSTIC verifier that checks any compliant receipt (whoever
signed it). The point is a network effect: if verifying "was this number checked?" is free and universal, the
receipt becomes the trust primitive of the agent economy — a number without a receipt starts to look unchecked.

Canonical form: `json.dumps(payload, sort_keys=True, separators=(",",":"))` → SHA-256 = `digest`; the signature
covers the digest. Ed25519 (public-verifiable) with the public key embedded, or HMAC (needs the shared secret).
"""
from __future__ import annotations
import json

from . import receipt as _rcpt

SPEC = "vcr/1"          # verifiable-claim-receipt, version 1
ALGORITHMS = ("ed25519", "hmac-sha256")
MAX_RECEIPT_BYTES = 128 * 1024   # cap untrusted receipts on the FREE public verify surface (unmetered)

# JSON-Schema-style description of a compliant receipt (documentation + structural validation, no jsonschema dep)
SCHEMA = {
    "$id": "https://numguard.dev/schemas/verifiable-claim-receipt/v1",
    "title": "Verifiable Claim Receipt",
    "type": "object",
    "required": ["payload", "digest", "signature"],
    "properties": {
        "payload": {
            "type": "object",
            "required": ["issuer", "claim", "nonce", "alg"],
            "properties": {
                "issuer": {"type": "string", "description": "who issued it (informational; trust flows from the key)"},
                "claim": {"type": "object", "description": "the checked claim: kind, survives, verdict"},
                "detail": {"type": "object", "description": "the full verification result"},
                "issued_at": {"type": ["integer", "null"], "description": "unix seconds"},
                "nonce": {"type": "string"},
                "alg": {"enum": list(ALGORITHMS)},
                "public_verifiable": {"type": "boolean"},
            },
        },
        "digest": {"type": "string", "description": "sha256 of the canonical payload (hex)"},
        "public_key": {"type": "string", "description": "Ed25519 public key (hex); '' for HMAC receipts"},
        "signature": {"type": "string", "description": "signature over the digest (hex)"},
    },
}


def validate(receipt: dict) -> dict:
    """Structural compliance with vcr/1 (does NOT check the signature — use verify_any for that). Returns
    {ok, errors}."""
    errors = []
    if not isinstance(receipt, dict):
        return {"ok": False, "errors": ["receipt must be an object"]}
    for k in ("payload", "digest", "signature"):
        if k not in receipt:
            errors.append(f"missing '{k}'")
    p = receipt.get("payload")
    if not isinstance(p, dict):
        errors.append("payload must be an object")
    else:
        for k in ("issuer", "claim", "nonce", "alg"):
            if k not in p:
                errors.append(f"payload missing '{k}'")
        if p.get("alg") not in ALGORITHMS:
            errors.append(f"alg must be one of {ALGORITHMS}")
        if p.get("alg") == "ed25519" and not receipt.get("public_key"):
            errors.append("ed25519 receipt must embed a public_key")
    return {"ok": not errors, "errors": errors}


def verify_any(receipt: dict, hmac_secret_hex: str | None = None) -> dict:
    """Verify ANY compliant receipt, issuer-agnostic — structural validity + the cryptographic signature against
    the receipt's OWN embedded public key. No numguard account, no network. For Ed25519 you need nothing but the
    receipt; for HMAC you need the shared secret. Returns a rich verdict."""
    try:                                        # free public endpoint: reject an oversized/unserializable blob
        if len(json.dumps(receipt, default=str)) > MAX_RECEIPT_BYTES:
            return {"valid": False, "reason": f"receipt exceeds {MAX_RECEIPT_BYTES} bytes", "spec": SPEC}
    except Exception:
        return {"valid": False, "reason": "receipt is not serializable", "spec": SPEC}
    v = validate(receipt)
    if not v["ok"]:
        return {"valid": False, "reason": "; ".join(v["errors"]), "spec": SPEC}
    ok = _rcpt.verify_receipt(receipt, hmac_secret_hex=hmac_secret_hex)
    p = receipt.get("payload", {})
    claim = p.get("claim", {})
    return {"valid": bool(ok), "spec": SPEC, "alg": p.get("alg"),
            "issuer": p.get("issuer"), "public_key": receipt.get("public_key", ""),
            "public_verifiable": p.get("alg") == "ed25519",
            "claim": claim, "issued_at": p.get("issued_at"),
            "reason": "signature valid; the claim + verdict are unaltered and were signed by the holder of this key"
                      if ok else "signature INVALID or payload tampered — do not trust this receipt"}


def describe() -> dict:
    """Discovery doc for the standard — what an implementer needs to issue/verify compliant receipts."""
    return {"spec": SPEC, "algorithms": list(ALGORITHMS),
            "canonical_form": "json.dumps(payload, sort_keys=True, separators=(',',':')) -> sha256 = digest; "
                              "signature covers the digest",
            "verify": "recompute the digest from payload, check the signature against the embedded public_key "
                      "(ed25519) — issuer-agnostic, offline, free",
            "schema": SCHEMA}


def _selftest():
    # a receipt from ANY keypair verifies with verify_any — no numguard-specific knowledge
    priv, pub = _rcpt.keypair()
    r = _rcpt.issue_receipt({"kind": "backtest", "survives": True, "verdict": "SR real"}, priv, pub)
    assert validate(r)["ok"], validate(r)
    assert verify_any(r)["valid"] is True
    r["payload"]["claim"]["survives"] = False          # tamper
    assert verify_any(r)["valid"] is False
    print(f"receipt_spec selftest: OK (spec={SPEC}, issuer-agnostic verify + tamper-reject)")


if __name__ == "__main__":
    _selftest()
