"""erc8004 — plug numguard into the ERC-8004 "Trustless Agents" standard as a REPUTATION provider.

ERC-8004 is live on 40+ chains (incl. Base) with a stable Reputation Registry. Its giveFeedback(...) posts a
signed numeric signal + tags + an off-chain payload URI+hash ABOUT an agentId — exactly the shape of a numguard
verdict. So a numguard receipt maps straight onto a reputation feedback signal, making a verified claim a
portable, composable on-chain reputation in the standard agents are adopting. This is "join the infra"
(distribution via the standard), not build a competing app.

numguard's differentiated slot: general providers (RNWY, Verity, Agent Veil, DJD) score behaviour/sybil/trust;
numguard verifies the specific NUMERIC claim (backtest Sharpe / eval gap / DSR) with a proof-of-check receipt —
the specialized check that feeds a giveFeedback signal, not a general trust score.

Exact on-chain interface (EIP-8004 Reputation Registry):
  giveFeedback(uint256 agentId, int128 value, uint8 valueDecimals,
               string tag1, string tag2, string endpoint, string feedbackURI, bytes32 feedbackHash)

`post_feedback(dry_run=True)` builds the exact calldata offline (no gas, testable). A real broadcast needs
NUMGUARD_GAS_KEY (the poster is a "client" giving feedback; it does NOT need to be a registered agent). Honest
scope: this is a reputation SIGNAL / proof_of_check — the value is +1/-1, the PROOF is the linked receipt
(offline-verifiable with only its public key). The Validation Registry is still experimental → untouched here.
"""
from __future__ import annotations
import os

# Deployed registries (verified from erc-8004-contracts). Same 0x8004… vanity across chains.
REPUTATION_REGISTRY = {"base": "0x8004BAa17C55a88189AE136b182e5fdA19dE9b63",
                       "base-sepolia": "0x8004B663056A597Dffe9eCcC1965A193B7388713"}
IDENTITY_REGISTRY = {"base": "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
                     "base-sepolia": "0x8004A818BFB912233c491871b3d84c89A494BD9e"}
CHAIN_IDS = {"base": 8453, "base-sepolia": 84532}
_RPCS = {"base": "https://mainnet.base.org", "base-sepolia": "https://sepolia.base.org"}

_GIVE_FEEDBACK_ABI = [{
    "name": "giveFeedback", "type": "function", "stateMutability": "nonpayable", "outputs": [],
    "inputs": [{"name": "agentId", "type": "uint256"}, {"name": "value", "type": "int128"},
               {"name": "valueDecimals", "type": "uint8"}, {"name": "tag1", "type": "string"},
               {"name": "tag2", "type": "string"}, {"name": "endpoint", "type": "string"},
               {"name": "feedbackURI", "type": "string"}, {"name": "feedbackHash", "type": "bytes32"}],
}]


def _signal(claim: dict):
    s = claim.get("survives")
    if s is True:
        return 1, "verified"
    if s is False:
        return -1, "flagged"
    return 0, "pending"


def to_feedback(receipt: dict, agent_id: int, uri_base: str = "", endpoint: str = "") -> dict:
    """Map a numguard vcr/1 receipt onto the EXACT giveFeedback arguments, ABOUT `agent_id`.
    value = +1 verified / -1 flagged / 0 pending; tag1='numguard', tag2='<status>:<kind>'; the FULL receipt is
    linked by feedbackURI + feedbackHash (=digest) so any reader fetches + independently verifies it for free."""
    if not isinstance(receipt, dict) or "payload" not in receipt or "digest" not in receipt:
        raise ValueError("need a numguard vcr/1 receipt (payload + digest)")
    claim = (receipt.get("payload") or {}).get("claim", {}) or {}
    value, status = _signal(claim)
    kind = str(claim.get("kind", "claim"))[:24]
    digest = receipt["digest"]
    uri = (uri_base.rstrip("/") + "/receipts/" + digest) if uri_base else ("numguard:receipt/" + digest)
    return {
        "agentId": int(agent_id),
        "value": value,
        "valueDecimals": 0,
        "tag1": "numguard",
        "tag2": f"{status}:{kind}"[:31],
        "endpoint": (endpoint or "")[:120],
        "feedbackURI": uri,
        "feedbackHash": "0x" + digest,
        "verdict": claim.get("verdict"),
        "data_source": (receipt.get("payload") or {}).get("data_source", "proof_of_check"),
    }


def post_feedback(receipt: dict, agent_id: int, *, network: str = "", dry_run: bool = True,
                  endpoint: str = "") -> dict:
    """Post a numguard verdict to the ERC-8004 Reputation Registry as agent reputation. `dry_run` (default)
    builds the exact calldata offline — no gas, no key, fully testable. A live post needs NUMGUARD_GAS_KEY.
    Never raises."""
    network = network or os.environ.get("NUMGUARD_NETWORK", "base")
    reg_addr = REPUTATION_REGISTRY.get(network)
    if not reg_addr:
        return {"error": f"no ERC-8004 Reputation Registry known for network {network!r}"}
    try:
        args = to_feedback(receipt, agent_id, uri_base=os.environ.get("PUBLIC_URL", ""), endpoint=endpoint)
    except Exception as e:
        return {"error": str(e)}
    call = [args["agentId"], args["value"], args["valueDecimals"], args["tag1"], args["tag2"],
            args["endpoint"], args["feedbackURI"], bytes.fromhex(args["feedbackHash"][2:])]
    try:
        from web3 import Web3
    except Exception:
        return {"error": "web3 not installed", "would_call": {"registry": reg_addr, **args}}

    contract = Web3().eth.contract(abi=_GIVE_FEEDBACK_ABI)
    try:
        calldata = contract.encode_abi("giveFeedback", args=call)
    except Exception:
        calldata = contract.encodeABI(fn_name="giveFeedback", args=call)   # older web3

    if dry_run:
        return {"dry_run": True, "network": network, "registry": reg_addr,
                "function": "giveFeedback", "args": args, "calldata": calldata,
                "note": "broadcast this calldata to the registry to post the reputation signal (needs gas). "
                        "The value is +1/-1; the PROOF is the linked receipt, offline-verifiable."}

    key = os.environ.get("NUMGUARD_GAS_KEY", "")
    if not key:
        return {"error": "live post unavailable (no gas key); use dry_run to get the calldata", "args": args}
    try:
        from eth_account import Account
        from . import settle as _settle
        rpc = os.environ.get("NUMGUARD_RPC", "") or _RPCS.get(network, "")
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 25}))
        acct = Account.from_key(key)
        reg = w3.eth.contract(address=Web3.to_checksum_address(reg_addr), abi=_GIVE_FEEDBACK_ABI)
        with _settle._SETTLE_LOCK:
            tx = reg.functions.giveFeedback(*call).build_transaction({
                "from": acct.address, "chainId": CHAIN_IDS.get(network, 8453),
                "nonce": w3.eth.get_transaction_count(acct.address, "pending")})
            signed = acct.sign_transaction(tx)
            txh = w3.eth.send_raw_transaction(getattr(signed, "raw_transaction", None) or signed.rawTransaction)
            rc = w3.eth.wait_for_transaction_receipt(txh, timeout=120)
        return {"posted": rc.status == 1, "tx": txh.hex(), "registry": reg_addr, "agentId": agent_id, "args": args}
    except Exception as e:
        return {"error": f"post failed: {str(e)[:160]}", "args": args}


def _selftest():
    from . import issue_receipt, keypair
    priv, pub = keypair()
    good = issue_receipt({"kind": "backtest", "survives": True, "verdict": "DSR 1.0 survives"}, priv, pub)
    bad = issue_receipt({"kind": "backtest", "survives": False, "verdict": "overfit"}, priv, pub)
    fg = to_feedback(good, agent_id=42, uri_base="https://numguard-4x7u.onrender.com")
    assert fg["value"] == 1 and fg["tag1"] == "numguard" and fg["tag2"] == "verified:backtest"
    assert fg["feedbackHash"] == "0x" + good["digest"] and fg["feedbackURI"].endswith(good["digest"])
    assert to_feedback(bad, 42)["value"] == -1 and to_feedback(bad, 42)["tag2"] == "flagged:backtest"
    d = post_feedback(good, 42, network="base", dry_run=True)
    ok = d.get("registry") == REPUTATION_REGISTRY["base"] and (d.get("calldata", "").startswith("0x") or "would_call" in d)
    assert ok, d
    print(f"erc8004 selftest: OK (verified->+1, flagged->-1, real ABI + Base registry {REPUTATION_REGISTRY['base'][:10]}…, "
          f"calldata {'encoded' if d.get('calldata') else '(web3 absent)'})")


if __name__ == "__main__":
    _selftest()
