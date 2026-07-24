"""anchor — turn a receipt into an on-chain, ownable credential (an RWA, not a coin).

A signed receipt already proves "this number was checked." Anchoring its digest on Base makes that an
IMMUTABLE, TIMESTAMPED, publicly-checkable fact: anyone can confirm on-chain that numguard's issuer committed
to this exact verification at this block. A numguard-attested track record then becomes a portable credential
other protocols can read — reputation as a real-world asset. No token, no speculation: the value is the
attestation, settled to the chain with the same gas wallet that settles payments.

MVP: write the digest into a self-transaction's calldata (tagged), immutable + timestamped. Roadmap: an EAS
attestation / registry contract for on-chain *composability* (other contracts reading it directly).

Requires web3 + NUMGUARD_GAS_KEY. Serializes the gas nonce with settlement so the two never collide.
"""
from __future__ import annotations
import os

CHAIN_IDS = {"base": 8453, "base-sepolia": 84532}
_RPCS = {"base": "https://mainnet.base.org", "base-sepolia": "https://sepolia.base.org"}
_EXPLORER = {"base": "https://basescan.org/tx/", "base-sepolia": "https://sepolia.basescan.org/tx/"}
_TAG = b"NGRCP1"                       # magic prefix so an anchor tx is identifiable in calldata


def available() -> bool:
    return bool(os.environ.get("NUMGUARD_GAS_KEY", ""))


def _calldata(digest_hex: str) -> bytes:
    """The anchor payload: tag + the 32-byte receipt digest."""
    d = digest_hex[2:] if digest_hex.startswith("0x") else digest_hex
    return _TAG + bytes.fromhex(d)


def anchor_digest(digest_hex: str, *, dry_run: bool = False) -> dict:
    """Anchor a receipt digest on-chain. `dry_run` builds the calldata + target without sending (for testing).
    Never raises — returns {error: ...} on any failure so a caller never crashes on the money path."""
    if not digest_hex or len(digest_hex.replace("0x", "")) != 64:
        return {"error": "digest must be a 32-byte sha256 hex"}
    network = os.environ.get("NUMGUARD_NETWORK", "base")
    data = _calldata(digest_hex)
    if dry_run:
        return {"dry_run": True, "network": network, "calldata": "0x" + data.hex(),
                "tag": _TAG.decode(), "digest": digest_hex}
    key = os.environ.get("NUMGUARD_GAS_KEY", "")
    if not key:
        return {"error": "on-chain anchoring is unavailable (no gas key configured)"}
    try:
        from web3 import Web3
        from eth_account import Account
        from . import settle as _settle           # reuse its gas-wallet lock so anchor + settle never race the nonce

        rpc = os.environ.get("NUMGUARD_RPC", "") or _RPCS.get(network, "")
        chain_id = CHAIN_IDS.get(network, 8453)
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20}))
        acct = Account.from_key(key)
        with _settle._SETTLE_LOCK:
            tx = {"from": acct.address, "to": acct.address, "value": 0, "data": "0x" + data.hex(),
                  "nonce": w3.eth.get_transaction_count(acct.address, "pending"), "chainId": chain_id}
            try:
                tx["gas"] = w3.eth.estimate_gas(tx)
            except Exception:
                tx["gas"] = 30000
            signed = acct.sign_transaction(tx)
            txh = w3.eth.send_raw_transaction(getattr(signed, "raw_transaction", None) or signed.rawTransaction)
        rcpt = w3.eth.wait_for_transaction_receipt(txh, timeout=90)
        if rcpt.get("status") != 1:
            return {"error": "anchor tx failed"}
        h = txh.hex() if not str(txh).startswith("0x") else txh.hex()
        return {"anchored": True, "network": network, "tx": h if str(h).startswith("0x") else "0x" + h,
                "block": rcpt.get("blockNumber"), "digest": digest_hex,
                "explorer": _EXPLORER.get(network, "") + (h if str(h).startswith("0x") else "0x" + h),
                "note": "immutable, timestamped on-chain proof that this receipt existed; a portable credential."}
    except Exception:
        return {"error": "anchor error"}      # opaque: never leak the RPC / internals


def _selftest():
    # dry-run builds correct tagged calldata without any key/network
    d = "ab" * 32
    r = anchor_digest(d, dry_run=True)
    assert r["dry_run"] and r["calldata"].startswith("0x" + _TAG.hex()) and d in r["calldata"]
    assert "error" in anchor_digest("tooshort")
    print(f"anchor selftest: OK (calldata tag={_TAG.decode()}, dry-run builds, bad-digest rejected)")


if __name__ == "__main__":
    _selftest()
