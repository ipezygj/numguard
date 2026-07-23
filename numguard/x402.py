"""x402 — pay-per-call the way an autonomous agent can, with no human in the loop.

The agent hits a tool, gets an HTTP 402 with a machine-readable price + pay-to address, pays in stablecoin
from its own wallet, retries with a payment header, and gets the result. This is the emerging agent-commerce
rail (HTTP 402 'Payment Required', Coinbase x402 et al.) and it fits a crypto/DeFi builder perfectly.

This module implements the PROTOCOL layer — the 402 challenge and the verify/settle gate. Actual on-chain
settlement is PLUGGABLE: inject a `verifier(x_payment)` that checks a facilitator API or an RPC and returns
whether the payment cleared. The default verifier refuses (so nothing is ever served unpaid by accident);
wire a real one for production. Everything here is stdlib-only.
"""
from __future__ import annotations
import json, os, time, hashlib
from dataclasses import dataclass, field
from typing import Callable, Optional

# 1 credit = $0.01; x402 prices quoted in the stablecoin's smallest honest unit (USDC has 6 decimals).
DEFAULT_NETWORK = "base"
DEFAULT_ASSET = "USDC"
USDC_DECIMALS = 6


def _usdc_units(usd: float) -> int:
    return int(round(usd * (10 ** USDC_DECIMALS)))


def payment_required(tool: str, price_usd: float, pay_to: str,
                     network: str = DEFAULT_NETWORK, asset: str = DEFAULT_ASSET,
                     resource: str = "", nonce: Optional[str] = None) -> dict:
    """The x402-shaped 402 body an agent can parse and pay. `accepts` lists payment options."""
    nonce = nonce or os.urandom(12).hex()
    return {
        "x402Version": 1,
        "error": "payment required",
        "resource": resource or f"numguard/{tool}",
        "accepts": [{
            "scheme": "exact",
            "network": network,
            "asset": asset,
            "maxAmountRequired": str(_usdc_units(price_usd)),   # smallest units
            "amountReadable": f"${price_usd:.4f} {asset}",
            "payTo": pay_to,
            "nonce": nonce,
            "mimeType": "application/json",
        }],
    }


@dataclass
class Settlement:
    valid: bool
    tx: str = ""
    amount_units: int = 0
    reason: str = ""


# A verifier takes the agent-supplied X-PAYMENT header (base64/JSON payment payload) + the challenge, and
# returns a Settlement. Real implementations call a facilitator (e.g. an x402 facilitator) or an RPC to
# confirm the transfer to `payTo` of >= maxAmountRequired referencing `nonce`.
Verifier = Callable[[str, dict], Settlement]


def _refuse_verifier(x_payment: str, challenge: dict) -> Settlement:
    return Settlement(False, reason="no settlement verifier wired — inject one (facilitator/RPC) for production")


def facilitator_verifier(facilitator_url: str = None):
    """A REAL x402 verifier that uses a facilitator's /verify + /settle endpoints (the standard flow: the
    facilitator checks the signed payment and broadcasts the USDC transfer on-chain). Point it at a public
    facilitator (env NUMGUARD_FACILITATOR_URL, e.g. an x402 facilitator on Base). The agent supplies a signed
    `X-PAYMENT` payload; this verifies it, settles it to your `payTo`, and returns the tx.

    Requires `httpx` (ships with the mcp SDK). Never raises — a failed call = an unsettled Settlement."""
    facilitator_url = facilitator_url or os.environ.get("NUMGUARD_FACILITATOR_URL", "")

    def verify(x_payment: str, challenge: dict) -> Settlement:
        if not facilitator_url:
            return Settlement(False, reason="NUMGUARD_FACILITATOR_URL not set")
        try:
            import base64, httpx
            # x402: X-PAYMENT is a base64 JSON payment payload
            try:
                payload = json.loads(base64.b64decode(x_payment))
            except Exception:
                payload = json.loads(x_payment)
            req = challenge["accepts"][0]
            body = {"x402Version": 1, "paymentPayload": payload, "paymentRequirements": req}
            with httpx.Client(timeout=20) as c:
                v = c.post(facilitator_url.rstrip("/") + "/verify", json=body)
                if v.status_code != 200 or not v.json().get("isValid", False):
                    return Settlement(False, reason=f"facilitator /verify rejected: {v.text[:120]}")
                s = c.post(facilitator_url.rstrip("/") + "/settle", json=body)
                sj = s.json() if s.status_code == 200 else {}
                if not sj.get("success", False):
                    return Settlement(False, reason=f"facilitator /settle failed: {s.text[:120]}")
                return Settlement(True, tx=sj.get("transaction", sj.get("txHash", "")),
                                  amount_units=int(req["maxAmountRequired"]))
        except Exception as e:
            return Settlement(False, reason=f"facilitator error: {str(e)[:120]}")

    return verify


def require_payment(tool: str, price_usd: float, pay_to: str, *,
                    x_payment: Optional[str] = None, verifier: Verifier = None,
                    network: str = DEFAULT_NETWORK) -> dict:
    """Server-side gate. No payment header -> return the 402 challenge. Header present -> verify + settle.
    Returns {'paid': True, 'settlement': ...} when cleared, else the 402 challenge (with any failure reason)."""
    challenge = payment_required(tool, price_usd, pay_to, network=network)
    if not x_payment:
        return {"status": 402, **challenge}
    v = (verifier or _refuse_verifier)(x_payment, challenge)
    if v.valid and v.amount_units >= _usdc_units(price_usd):
        return {"status": 200, "paid": True,
                "settlement": {"tx": v.tx, "amount_units": v.amount_units, "network": network}}
    ch = {"status": 402, **challenge}
    ch["accepts"][0]["failure"] = v.reason or "payment not verified / underpaid"
    return ch


def _selftest():
    ch = require_payment("verify_backtest", 0.03, "0xPayToAddress")
    assert ch["status"] == 402 and ch["accepts"][0]["maxAmountRequired"] == str(_usdc_units(0.03))
    # a stub verifier that clears a well-formed payment
    def ok_verifier(xp, challenge):
        need = int(challenge["accepts"][0]["maxAmountRequired"])
        return Settlement(True, tx="0xabc", amount_units=need)
    paid = require_payment("verify_backtest", 0.03, "0xPayTo", x_payment="stub-header", verifier=ok_verifier)
    assert paid["status"] == 200 and paid["paid"]
    # default verifier refuses (never serve unpaid by accident)
    refused = require_payment("verify_backtest", 0.03, "0xPayTo", x_payment="stub-header")
    assert refused["status"] == 402
    print("x402 selftest: OK (402 challenge, verify-clears, default-refuses)")


if __name__ == "__main__":
    _selftest()
