"""settle — numguard settles x402 payments itself, no external facilitator.

The agent signs a USDC EIP-3009 `transferWithAuthorization` (gasless for the payer). numguard verifies that
signature, checks the payer's on-chain balance over a public RPC, then submits the authorization to the USDC
contract — paying gas from a dedicated gas wallet (env `NUMGUARD_GAS_KEY`). USDC moves straight from the payer
to your receiving wallet. This removes the separate facilitator service (and its fragile internal networking):
one public service settles on its own.

Security: `NUMGUARD_GAS_KEY` is a DEDICATED hot wallet holding only a little Base ETH for gas — never your
receiving wallet. If it's unset, the verifier is unavailable and the caller falls back to refusing (nothing is
ever served unpaid). The payer's USDC is only ever pulled to your `payTo` via their own signed authorization.

Env: NUMGUARD_GAS_KEY (gas wallet private key) · NUMGUARD_RPC (Base RPC, default public) · NUMGUARD_NETWORK.
Requires web3 + eth_account (extra: `pip install numguard[settle]`).
"""
from __future__ import annotations
import base64, json, os, threading, time

from .x402 import Settlement

# Serialize on-chain settlement: dedup an authorization by its EIP-3009 nonce (so a replayed X-PAYMENT is
# broadcast at most once — not N times burning gas on N-1 reverts) and serialize gas-wallet nonce allocation
# (so two concurrent settlements can't grab the same nonce and collide). The lock is held only for the
# balance-check + build + send, NOT the on-chain receipt wait.
_SETTLE_LOCK = threading.Lock()
_SEEN_NONCES: dict = {}
_SEEN_TTL = 180.0

CHAIN_IDS = {"base": 8453, "base-sepolia": 84532}
_RPCS = {"base": "https://mainnet.base.org", "base-sepolia": "https://sepolia.base.org"}

# minimal USDC ABI: EIP-3009 transferWithAuthorization (v,r,s variant) + balanceOf + authorizationState + Transfer
_ABI = [
    {"name": "transferWithAuthorization", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"}, {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"},
                {"name": "v", "type": "uint8"}, {"name": "r", "type": "bytes32"}, {"name": "s", "type": "bytes32"}],
     "outputs": []},
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "authorizationState", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "authorizer", "type": "address"}, {"name": "nonce", "type": "bytes32"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"name": "Transfer", "type": "event", "anonymous": False,
     "inputs": [{"name": "from", "type": "address", "indexed": True},
                {"name": "to", "type": "address", "indexed": True},
                {"name": "value", "type": "uint256", "indexed": False}]},
]


def available() -> bool:
    """True when a gas key is configured, so numguard can settle on its own."""
    return bool(os.environ.get("NUMGUARD_GAS_KEY", ""))


def _typed_data(auth: dict, chain_id: int, usdc: str, name: str, version: str) -> dict:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"}, {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"}],
        },
        "domain": {"name": name, "version": version, "chainId": chain_id, "verifyingContract": usdc},
        "primaryType": "TransferWithAuthorization",
        "message": {"from": auth["from"], "to": auth["to"], "value": int(auth["value"]),
                    "validAfter": int(auth["validAfter"]), "validBefore": int(auth["validBefore"]),
                    "nonce": auth["nonce"]},
    }


def self_verifier():
    """A Verifier (x_payment, challenge) -> Settlement that settles on-chain itself. Returns None if no gas key
    is configured (caller then falls back to refusing). Never raises."""
    key = os.environ.get("NUMGUARD_GAS_KEY", "")
    if not key:
        return None
    network = os.environ.get("NUMGUARD_NETWORK", "base")
    rpc = os.environ.get("NUMGUARD_RPC", "") or _RPCS.get(network, "")
    chain_id = CHAIN_IDS.get(network, 8453)

    def verify(x_payment: str, challenge: dict) -> Settlement:
        try:
            from web3 import Web3
            from eth_account import Account
            from eth_account.messages import encode_typed_data

            req = challenge["accepts"][0]
            try:
                payload = json.loads(base64.b64decode(x_payment))
            except Exception:
                payload = json.loads(x_payment)
            auth = payload["payload"]["authorization"]
            sig = payload["payload"]["signature"]

            usdc = Web3.to_checksum_address(req["asset"])
            pay_to = Web3.to_checksum_address(req["payTo"])
            need = int(req["maxAmountRequired"])
            extra = req.get("extra", {})

            # 1. the signature must recover to the payer over the exact EIP-712 authorization
            typed = _typed_data(auth, chain_id, usdc, extra.get("name", ""), extra.get("version", ""))
            signer = Account.recover_message(encode_typed_data(full_message=typed), signature=sig)
            if signer.lower() != str(auth["from"]).lower():
                return Settlement(False, reason="signature does not match the payer (from)")

            # 2. the authorization must actually pay us, in full, right now
            if Web3.to_checksum_address(auth["to"]) != pay_to:
                return Settlement(False, reason="authorization payTo mismatch")
            if int(auth["value"]) < need:
                return Settlement(False, reason=f"underpaid: {auth['value']} < {need}")
            now = int(time.time())
            if not (int(auth["validAfter"]) <= now < int(auth["validBefore"])):
                return Settlement(False, reason="authorization not currently valid (validAfter/validBefore)")

            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20}))
            token = w3.eth.contract(address=usdc, abi=_ABI)
            gas_acct = Account.from_key(key)
            raw = bytes.fromhex(sig[2:] if sig.startswith("0x") else sig)
            r, s, v = raw[:32], raw[32:64], raw[64]
            if v < 27:
                v += 27
            nonce_b = bytes.fromhex(auth["nonce"][2:] if str(auth["nonce"]).startswith("0x") else auth["nonce"])
            fn = token.functions.transferWithAuthorization(
                Web3.to_checksum_address(auth["from"]), pay_to, int(auth["value"]),
                int(auth["validAfter"]), int(auth["validBefore"]), nonce_b, v, r, s)
            nonce_key = str(auth["nonce"]).lower()

            # 3+4. dedup, balance-gate, and broadcast under the lock (one authorization broadcast at most once;
            # gas nonce serialized). The receipt wait happens AFTER the lock is released.
            payer = Web3.to_checksum_address(auth["from"])
            with _SETTLE_LOCK:
                seen = _SEEN_NONCES.get(nonce_key)
                if seen is not None and (time.time() - seen) < _SEEN_TTL:
                    return Settlement(False, reason="payment already being processed")
                # M6: if this EIP-3009 nonce is already consumed on-chain, transferWithAuthorization would
                # revert — don't broadcast a doomed tx and burn gas.
                if token.functions.authorizationState(payer, nonce_b).call():
                    return Settlement(False, reason="authorization already used")
                bal = token.functions.balanceOf(payer).call()
                if bal < need:   # unfunded payer: don't mark the nonce seen, so a funded retry can proceed
                    return Settlement(False, reason=f"insufficient funds: payer USDC balance {bal} < {need}")
                _SEEN_NONCES[nonce_key] = time.time()
                for k in [k for k, t in list(_SEEN_NONCES.items()) if (time.time() - t) > _SEEN_TTL]:
                    _SEEN_NONCES.pop(k, None)
                tx = fn.build_transaction({"from": gas_acct.address,
                                           "nonce": w3.eth.get_transaction_count(gas_acct.address, "pending"),
                                           "chainId": chain_id})
                signed = gas_acct.sign_transaction(tx)
                txh = w3.eth.send_raw_transaction(getattr(signed, "raw_transaction", None) or signed.rawTransaction)

            rcpt = w3.eth.wait_for_transaction_receipt(txh, timeout=90)
            if rcpt.get("status") != 1:
                return Settlement(False, reason="settlement failed")   # opaque: no internal detail to the caller
            # M7: don't trust a single RPC's status flag alone — confirm the receipt actually carries a USDC
            # Transfer(payer -> payTo) of >= need before serving.
            got = 0
            try:
                from web3.logs import DISCARD
                for ev in token.events.Transfer().process_receipt(rcpt, errors=DISCARD):
                    a = ev["args"]
                    if a["from"] == payer and a["to"] == pay_to:
                        got += int(a["value"])
            except Exception:
                got = -1
            if got < need:
                return Settlement(False, reason="settlement unconfirmed")
            return Settlement(True, tx=txh.hex(), amount_units=need)
        except Exception:
            return Settlement(False, reason="settlement error")        # opaque: never leak the RPC URL / internals

    return verify
