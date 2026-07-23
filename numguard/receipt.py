"""receipt — a portable, tamper-evident 'this number was checked' token.

When numguard verifies a claim, it can hand back a signed receipt. An agent attaches it to its output; any
downstream agent or human can verify — with only the issuer's PUBLIC key — that (a) the exact claim and
verdict were not altered, and (b) numguard actually issued it. That is the moat: as receipts circulate, the
verification standard is yours, and a number without a receipt starts to look like a number nobody checked.

Signing is Ed25519 (asymmetric → public verifiability) when `cryptography` is available, else an HMAC
fallback (symmetric → the verifier needs the shared secret; fine for a single trust domain, not for public
verification). The canonical payload is `json.dumps(sort_keys=True)`; the signature covers its SHA-256.
"""
from __future__ import annotations
import hashlib, hmac, json, os, time

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    from cryptography.hazmat.primitives import serialization
    _ED = True
except Exception:                          # pragma: no cover
    _ED = False

ISSUER = "numguard"


def _canon(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _digest(payload: dict) -> str:
    return hashlib.sha256(_canon(payload)).hexdigest()


# ------------------------------- Ed25519 keys ------------------------------- #
def keypair() -> tuple[str, str]:
    """Generate an issuer keypair; returns (private_hex, public_hex). Ed25519 if available."""
    if not _ED:
        # HMAC 'keys': the private is a random secret; public is unused (verify needs the secret)
        return os.urandom(32).hex(), ""
    sk = Ed25519PrivateKey.generate()
    priv = sk.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                            serialization.NoEncryption()).hex()
    pub = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    return priv, pub


def issue_receipt(claim_result: dict, private_hex: str, public_hex: str = "",
                  ttl_seconds: int | None = None) -> dict:
    """Wrap a verify_claim result in a signed receipt."""
    public = bool(_ED and public_hex)
    issued = int(time.time())
    payload = {
        "issuer": ISSUER,
        "claim": {k: claim_result.get(k) for k in ("kind", "survives", "verdict")},
        "detail": {k: v for k, v in claim_result.items() if k not in ("verdict",)},
        "issued_at": issued,
        "nonce": os.urandom(8).hex(),
        "alg": "ed25519" if public else "hmac-sha256",
        # explicit so a consumer never mistakes a symmetric HMAC receipt (needs the shared secret) for a
        # publicly-verifiable one — the moat silently degrading to HMAC would otherwise look identical.
        "public_verifiable": public,
    }
    if ttl_seconds:
        payload["expires_at"] = issued + int(ttl_seconds)
    digest = _digest(payload)
    if _ED and public_hex:
        sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
        sig = sk.sign(bytes.fromhex(digest)).hex()
        return {"payload": payload, "digest": digest, "public_key": public_hex, "signature": sig}
    sig = hmac.new(bytes.fromhex(private_hex), bytes.fromhex(digest), hashlib.sha256).hexdigest()
    return {"payload": payload, "digest": digest, "public_key": "", "signature": sig}


def verify_receipt(receipt: dict, hmac_secret_hex: str | None = None) -> bool:
    """True iff the receipt's payload is unaltered and the signature is valid. Ed25519 needs no secret;
    the HMAC fallback needs the shared secret via `hmac_secret_hex`."""
    try:
        payload, sig = receipt["payload"], receipt["signature"]
        if _digest(payload) != receipt.get("digest"):
            return False
        if payload.get("alg") == "ed25519":
            if not _ED:
                return False
            pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(receipt["public_key"]))
            pk.verify(bytes.fromhex(sig), bytes.fromhex(receipt["digest"]))
            return True
        if hmac_secret_hex is None:
            return False
        exp = hmac.new(bytes.fromhex(hmac_secret_hex), bytes.fromhex(receipt["digest"]), hashlib.sha256).hexdigest()
        return hmac.compare_digest(exp, sig)
    except Exception:
        return False


def _selftest():
    from .claims import verify_claim
    res = verify_claim("backtest", sr=0.12, T=250, n_trials=100)
    priv, pub = keypair()
    r = issue_receipt(res, priv, pub)
    assert verify_receipt(r, hmac_secret_hex=priv), "valid receipt must verify"
    # tamper: flip the verdict -> must fail
    r2 = json.loads(json.dumps(r)); r2["payload"]["claim"]["survives"] = True
    assert not verify_receipt(r2, hmac_secret_hex=priv), "tampered receipt must fail"
    print(f"receipt selftest: OK (alg={r['payload']['alg']}, verify+tamper-detect)")


if __name__ == "__main__":
    _selftest()
