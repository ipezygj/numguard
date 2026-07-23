"""handshake_test — prove the x402 payment FORMAT is accepted, for $0 and with no real wallet.

Generates a THROWAWAY key (no funds, never a real user's key), fetches the live 402 challenge, signs a
correct EIP-3009 USDC transfer authorization for it, and POSTs it back with an X-PAYMENT header. Because the
throwaway wallet holds no USDC, the facilitator's /verify returns "insufficient funds" — which proves the
scheme/network/signature FORMAT parsed correctly (a malformed payment fails earlier, with a different error).

Usage:  python scripts/handshake_test.py [endpoint_url]
"""
import base64, json, secrets, sys, time

import httpx
from eth_account import Account

URL = sys.argv[1] if len(sys.argv) > 1 else "https://numguard-4x7u.onrender.com/verify_backtest"
BODY = {"sr": 0.12, "T": 250, "n_trials": 100}
CHAIN_ID = 8453  # Base mainnet


def sign_authorization(acct, req):
    now = int(time.time())
    auth = {
        "from": acct.address,
        "to": req["payTo"],
        "value": int(req["maxAmountRequired"]),
        "validAfter": 0,
        "validBefore": now + 3600,
        "nonce": "0x" + secrets.token_hex(32),
    }
    typed = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"}, {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"},
            ],
        },
        "domain": {"name": req["extra"]["name"], "version": req["extra"]["version"],
                   "chainId": CHAIN_ID, "verifyingContract": req["asset"]},
        "primaryType": "TransferWithAuthorization",
        "message": auth,
    }
    signed = Account.sign_typed_data(acct.key, full_message=typed)
    # wire payload: numbers as strings (x402 v1 convention)
    wire_auth = {**auth, "value": str(auth["value"]),
                 "validAfter": str(auth["validAfter"]), "validBefore": str(auth["validBefore"])}
    return {"signature": "0x" + signed.signature.hex().lstrip("0x"), "authorization": wire_auth}


def main():
    acct = Account.create()  # throwaway, holds nothing
    print(f"throwaway payer (no funds): {acct.address}\n")

    with httpx.Client(timeout=30) as c:
        r = c.post(URL, json=BODY)
        print(f"[1] unpaid  -> HTTP {r.status_code}")
        ch = r.json()
        req = ch["accepts"][0]
        print(f"    accepts: scheme={req['scheme']} network={req['network']} amount={req['maxAmountRequired']} extra={req.get('extra')}\n")

        payload = {"x402Version": 1, "scheme": req["scheme"], "network": req["network"],
                   "payload": sign_authorization(acct, req)}
        x_payment = base64.b64encode(json.dumps(payload).encode()).decode()

        r2 = c.post(URL, json=BODY, headers={"X-PAYMENT": x_payment})
        print(f"[2] paid    -> HTTP {r2.status_code}")
        out = r2.json()

    # the facilitator's rejection reason lands in accepts[0].failure (or a 200 = settled)
    reason = ""
    try:
        reason = out["accepts"][0].get("failure", "")
    except Exception:
        reason = json.dumps(out)[:400]
    print(f"    facilitator reason: {reason!r}\n")

    low = reason.lower()
    if r2.status_code == 200 and out.get("paid"):
        print("VERDICT: settled (unexpected for an unfunded wallet - but the whole path works end to end).")
    elif any(k in low for k in ("insufficient", "balance", "funds", "erc20")):
        print("VERDICT: [OK] FORMAT CONFIRMED - facilitator parsed + verified the payment shape and only")
        print("         rejected it for no USDC. A funded wallet would settle. Handshake is correct.")
    elif not reason:
        print("VERDICT: [??] no failure reason returned - inspect the full response.")
    else:
        print("VERDICT: [!!] FORMAT/OTHER rejection - reason above. Adjust and retry.")


if __name__ == "__main__":
    main()
