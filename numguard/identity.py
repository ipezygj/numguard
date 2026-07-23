"""identity — numguard's stable ISSUER identity, so an agent can authoritatively fetch the one public
key that verifies every numguard receipt (the anchor for `/pubkey` and `.well-known`).

Why this matters for agent trust: a receipt is only proof if the verifier knows the issuer's *canonical*
public key. A key that regenerates on every restart (the old behaviour on a diskless host) would make
yesterday's receipts unverifiable and let anyone stand up a look-alike. So the key is resolved in a fixed
order and its id is published:

  1. NUMGUARD_ISSUER_KEY  — the Ed25519 private key (hex), pinned via env. USE THIS ON A HOSTED DEPLOY
                            (diskless): the identity survives restarts/redeploys.
  2. ~/.numguard/issuer.json ({"priv","pub"}) — the local file mcp_server already uses (shared, so the
                            MCP and REST rails sign with the SAME key).
  3. generate + persist to that file (local dev).

Only the PUBLIC key and key_id ever leave the box. `key_id` = first 16 hex chars of the public key — a
short, stable handle an agent can pin.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import receipt as _rcpt

_KEYFILE = Path(os.environ.get("NUMGUARD_ISSUER", Path.home() / ".numguard" / "issuer.json"))

ISSUER_NAME = os.environ.get("NUMGUARD_ISSUER_NAME", "numguard")


def _pub_from_priv(priv_hex: str) -> str:
    """Derive the Ed25519 public key (hex) from a private key (hex). Empty string if crypto is unavailable
    (HMAC fallback mode — there is no asymmetric public key then)."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
        return sk.public_key().public_bytes(serialization.Encoding.Raw,
                                            serialization.PublicFormat.Raw).hex()
    except Exception:
        return ""


def issuer() -> tuple[str, str]:
    """(private_hex, public_hex) for the stable issuer identity. Resolves env -> file -> generate."""
    env = os.environ.get("NUMGUARD_ISSUER_KEY", "").strip()
    if env:
        return env, _pub_from_priv(env)
    try:
        d = json.loads(_KEYFILE.read_text(encoding="utf-8"))
        return d["priv"], d["pub"]
    except Exception:
        priv, pub = _rcpt.keypair()
        try:
            _KEYFILE.parent.mkdir(parents=True, exist_ok=True)
            _KEYFILE.write_text(json.dumps({"priv": priv, "pub": pub}), encoding="utf-8")
        except Exception:
            pass
        return priv, pub


def public_key() -> str:
    return issuer()[1]


def key_id(pub: str | None = None) -> str:
    """Short stable handle for the issuer key (first 16 hex of the public key)."""
    pub = public_key() if pub is None else pub
    return pub[:16] if pub else "hmac-local"


def identity_card() -> dict:
    """The public identity document served at /pubkey and referenced by .well-known."""
    pub = public_key()
    return {
        "issuer": ISSUER_NAME,
        "algorithm": "ed25519" if pub else "hmac-sha256 (local, not publicly verifiable)",
        "public_key": pub,
        "key_id": key_id(pub),
        "verify": ("Verify any numguard receipt with ONLY this key: check the Ed25519 signature over "
                   "SHA-256 of json.dumps(payload, sort_keys=True, separators=(',',':')). "
                   "numguard.receipt.verify_receipt does this for you."),
        "public_verifiable": bool(pub),
    }
