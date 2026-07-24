# Verifiable Claim Receipt — open standard `vcr/1`

A portable, tamper-evident token that says **"this number was checked."** Any issuer can produce one; **anyone
can verify it** with only a public key — offline, free, no account, no numguard. That universality is the point:
if checking "was this claim verified, and by whom?" is free and standard, a number without a receipt starts to
look like a number nobody checked.

## Shape

```json
{
  "payload": {
    "issuer": "numguard",
    "claim":  { "kind": "backtest", "survives": false, "verdict": "…one line…" },
    "detail": { "…the full verification result…" },
    "issued_at": 1750000000,
    "nonce": "…hex…",
    "alg": "ed25519",
    "public_verifiable": true
  },
  "digest": "…sha256(canonical payload), hex…",
  "public_key": "…ed25519 public key, hex…",
  "signature": "…signature over the digest, hex…"
}
```

- **Canonical form:** `json.dumps(payload, sort_keys=True, separators=(",", ":"))`.
- **Digest:** `sha256(canonical)` in hex.
- **Signature:** over the *digest bytes*. `alg` is `ed25519` (public-verifiable; the public key is embedded) or
  `hmac-sha256` (symmetric; the verifier needs the shared secret).

## Verify (issuer-agnostic)

1. Recompute `digest = sha256(canonical(payload))`; it must equal `receipt.digest`.
2. `ed25519`: check `signature` against `receipt.public_key` over the digest. **No issuer knowledge needed** —
   trust flows from the key, not the `issuer` string.
3. `hmac-sha256`: recompute the HMAC with the shared secret and compare.

Tampering with any field changes the canonical payload → the digest → verification fails.

## Reference implementation

`numguard/receipt.py` (issue + verify) and `numguard/receipt_spec.py` (`validate`, `verify_any`, `describe`,
`SCHEMA`). Free, universal verification is exposed everywhere:

- MCP tools `verify_receipt(receipt)` and `receipt_spec()` — no api_key, no charge.
- HTTP `POST /verify_receipt`, `GET /receipt_spec` — public, free.

```python
from numguard.receipt_spec import verify_any
verify_any(some_receipt)   # {"valid": true, "issuer": "...", "claim": {...}, "public_key": "..."}
```

Issuing a receipt for a claim numguard actually checked (so it attests something real) is the paid, metered
part; **verifying** anyone's receipt is free — that asymmetry is what makes the standard spread.
