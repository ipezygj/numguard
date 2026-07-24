"""eas — numguard verification credentials as on-chain EAS attestations (composable reputation, not a coin).

The `anchor` module proves a receipt EXISTED (timestamped in calldata). This goes further: it writes a proper
Ethereum Attestation Service attestation on Base, so a numguard verdict becomes a QUERYABLE, COMPOSABLE on-chain
credential — any protocol can ask EAS "does this agent's address hold a numguard attestation, and what was the
verdict?" and gate/allocate/collateralize on it. Reputation as a real-world asset, in the industry-standard
attestation registry, with no token and no speculation.

Addresses are the OP-Stack predeploys (identical on Base mainnet 8453 and Base Sepolia 84532). Schema:
`bytes32 digest,string kind,bool survives,string verdict` — general enough for any numguard receipt. Requires
web3 + eth_abi + NUMGUARD_GAS_KEY; the attest tx shares the settlement gas-nonce lock so they never collide.
"""
from __future__ import annotations
import os, threading, time

EAS_ADDRESS = "0x4200000000000000000000000000000000000021"
SCHEMA_REGISTRY = "0x4200000000000000000000000000000000000020"
CHAIN_IDS = {"base": 8453, "base-sepolia": 84532}
_RPCS = {"base": "https://mainnet.base.org", "base-sepolia": "https://sepolia.base.org"}
_EASSCAN = {"base": "https://base.easscan.org/attestation/view/",
            "base-sepolia": "https://base-sepolia.easscan.org/attestation/view/"}

# the numguard verification-credential schema (field order is load-bearing — encode + decode must match)
SCHEMA_STRING = "bytes32 digest,string kind,bool survives,string verdict"
SCHEMA_TYPES = ["bytes32", "string", "bool", "string"]
RESOLVER = "0x0000000000000000000000000000000000000000"
REVOCABLE = True

_EAS_ABI = [
    {"type": "function", "name": "attest", "stateMutability": "payable",
     "inputs": [{"name": "request", "type": "tuple", "components": [
         {"name": "schema", "type": "bytes32"},
         {"name": "data", "type": "tuple", "components": [
             {"name": "recipient", "type": "address"}, {"name": "expirationTime", "type": "uint64"},
             {"name": "revocable", "type": "bool"}, {"name": "refUID", "type": "bytes32"},
             {"name": "data", "type": "bytes"}, {"name": "value", "type": "uint256"}]}]}],
     "outputs": [{"name": "", "type": "bytes32"}]},
    {"type": "event", "name": "Attested", "anonymous": False, "inputs": [
        {"name": "recipient", "type": "address", "indexed": True},
        {"name": "attester", "type": "address", "indexed": True},
        {"name": "uid", "type": "bytes32", "indexed": False},
        {"name": "schemaUID", "type": "bytes32", "indexed": True}]},
    {"type": "function", "name": "getAttestation", "stateMutability": "view",
     "inputs": [{"name": "uid", "type": "bytes32"}],
     "outputs": [{"name": "", "type": "tuple", "components": [
         {"name": "uid", "type": "bytes32"}, {"name": "schema", "type": "bytes32"},
         {"name": "time", "type": "uint64"}, {"name": "expirationTime", "type": "uint64"},
         {"name": "revocationTime", "type": "uint64"}, {"name": "refUID", "type": "bytes32"},
         {"name": "recipient", "type": "address"}, {"name": "attester", "type": "address"},
         {"name": "revocable", "type": "bool"}, {"name": "data", "type": "bytes"}]}]},
]
_REGISTRY_ABI = [
    {"type": "function", "name": "register", "stateMutability": "nonpayable",
     "inputs": [{"name": "schema", "type": "string"}, {"name": "resolver", "type": "address"},
                {"name": "revocable", "type": "bool"}], "outputs": [{"name": "", "type": "bytes32"}]},
    {"type": "function", "name": "getSchema", "stateMutability": "view",
     "inputs": [{"name": "uid", "type": "bytes32"}], "outputs": [{"name": "", "type": "tuple", "components": [
         {"name": "uid", "type": "bytes32"}, {"name": "resolver", "type": "address"},
         {"name": "revocable", "type": "bool"}, {"name": "schema", "type": "string"}]}]},
]

_ZERO32 = b"\x00" * 32

# free on-chain reads (check_attestation) hit an external RPC and are unmetered — cache immutable results and
# globally rate-limit cache-MISSES so a spammer can't hammer the public RPC (which could rate-limit our IP and
# break settlements/anchors).
_READ_CACHE: dict = {}
_READ_TTL = 60.0
_READ_LOCK = threading.Lock()
_READ_HITS: list = []
_READ_RATE, _READ_WINDOW = 60, 60.0


def available() -> bool:
    return bool(os.environ.get("NUMGUARD_GAS_KEY", ""))


def schema_uid() -> str:
    """The deterministic EAS schema UID for numguard's credential schema (keccak of the packed triple)."""
    from eth_abi.packed import encode_packed
    from eth_utils import keccak
    return "0x" + keccak(encode_packed(["string", "address", "bool"],
                                       [SCHEMA_STRING, RESOLVER, REVOCABLE])).hex()


def _encode_credential(digest_hex: str, kind: str, survives: bool, verdict: str) -> bytes:
    from eth_abi import encode
    d = digest_hex[2:] if digest_hex.startswith("0x") else digest_hex
    return encode(SCHEMA_TYPES, [bytes.fromhex(d), str(kind or ""), bool(survives), str(verdict or "")[:400]])


def attest_credential(digest_hex: str, kind: str, survives, verdict: str, *,
                      recipient: str = "", dry_run: bool = False) -> dict:
    """Write a numguard verification credential to EAS on Base. `recipient` = the agent's on-chain address the
    credential is about (so protocols can look it up by that address); defaults to the attester. `dry_run`
    builds the encoded data + schema UID without sending. Never raises."""
    if not digest_hex or len(digest_hex.replace("0x", "")) != 64:
        return {"error": "digest must be a 32-byte sha256 hex"}
    network = os.environ.get("NUMGUARD_NETWORK", "base")
    try:
        data = _encode_credential(digest_hex, kind, bool(survives), verdict)
    except Exception:
        return {"error": "could not encode credential"}
    if dry_run:
        return {"dry_run": True, "network": network, "schema_uid": schema_uid(),
                "encoded_data": "0x" + data.hex(), "schema": SCHEMA_STRING, "recipient": recipient or "(attester)"}
    key = os.environ.get("NUMGUARD_GAS_KEY", "")
    if not key:
        return {"error": "on-chain attestation is unavailable (no gas key configured)"}
    try:
        from web3 import Web3
        from eth_account import Account
        from . import settle as _settle

        rpc = os.environ.get("NUMGUARD_RPC", "") or _RPCS.get(network, "")
        chain_id = CHAIN_IDS.get(network, 8453)
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 25}))
        acct = Account.from_key(key)
        eas = w3.eth.contract(address=Web3.to_checksum_address(EAS_ADDRESS), abi=_EAS_ABI)
        reg = w3.eth.contract(address=Web3.to_checksum_address(SCHEMA_REGISTRY), abi=_REGISTRY_ABI)
        uid = schema_uid()
        rcpt_addr = Web3.to_checksum_address(recipient) if recipient else acct.address
        request = (bytes.fromhex(uid[2:]),
                   (rcpt_addr, 0, REVOCABLE, _ZERO32, data, 0))

        with _settle._SETTLE_LOCK:
            # register the schema once (idempotent: skip if it already exists)
            try:
                existing = reg.functions.getSchema(bytes.fromhex(uid[2:])).call()
                registered = existing[0] != _ZERO32
            except Exception:
                registered = False
            if not registered:
                rtx = reg.functions.register(SCHEMA_STRING, Web3.to_checksum_address(RESOLVER), REVOCABLE
                                             ).build_transaction({
                    "from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
                    "chainId": chain_id})
                rs = acct.sign_transaction(rtx)
                rh = w3.eth.send_raw_transaction(getattr(rs, "raw_transaction", None) or rs.rawTransaction)
                w3.eth.wait_for_transaction_receipt(rh, timeout=90)
            tx = eas.functions.attest(request).build_transaction({
                "from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
                "chainId": chain_id, "value": 0})
            signed = acct.sign_transaction(tx)
            txh = w3.eth.send_raw_transaction(getattr(signed, "raw_transaction", None) or signed.rawTransaction)
        rec = w3.eth.wait_for_transaction_receipt(txh, timeout=120)
        if rec.get("status") != 1:
            return {"error": "attestation tx failed"}
        att_uid = ""
        try:
            for ev in eas.events.Attested().process_receipt(rec):
                att_uid = ev["args"]["uid"].hex()
        except Exception:
            att_uid = ""
        att_uid = ("0x" + att_uid) if att_uid and not att_uid.startswith("0x") else att_uid
        txhex = txh.hex() if str(txh.hex()).startswith("0x") else "0x" + txh.hex()
        return {"attested": True, "network": network, "attestation_uid": att_uid, "schema_uid": uid,
                "recipient": rcpt_addr, "tx": txhex, "block": rec.get("blockNumber"),
                "easscan": (_EASSCAN.get(network, "") + att_uid) if att_uid else "",
                "note": "a composable on-chain credential — any protocol can look up this attestation by the "
                        "recipient address and read the numguard verdict."}
    except Exception:
        return {"error": "attestation error"}


def read_attestation(uid: str) -> dict:
    """Read a numguard credential back FROM EAS on Base — the composability payoff: any protocol/agent can look
    up an attestation UID and read the verdict, no gas, no numguard account. Confirms it's a genuine numguard
    attestation (schema match), decodes the credential, and reports if it was revoked. Never raises."""
    if not uid or len(uid.replace("0x", "")) != 64:
        return {"error": "uid must be a 32-byte hex"}
    network = os.environ.get("NUMGUARD_NETWORK", "base")
    key = uid.lower()
    with _READ_LOCK:                              # serve immutable reads from cache; rate-limit RPC misses
        cached = _READ_CACHE.get(key)
        if cached and (time.time() - cached[1]) < _READ_TTL:
            return cached[0]
        now = time.time()
        _READ_HITS[:] = [t for t in _READ_HITS if now - t < _READ_WINDOW]
        if len(_READ_HITS) >= _READ_RATE:
            return {"error": "read rate limit reached, retry shortly"}
        _READ_HITS.append(now)
    try:
        from web3 import Web3
        from eth_abi import decode as _decode

        rpc = os.environ.get("NUMGUARD_RPC", "") or _RPCS.get(network, "")
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20}))
        eas = w3.eth.contract(address=Web3.to_checksum_address(EAS_ADDRESS), abi=_EAS_ABI)
        a = eas.functions.getAttestation(bytes.fromhex(uid[2:] if uid.startswith("0x") else uid)).call()
        att_uid, schema, t, exp, revoked_at, ref, recipient, attester, revocable, data = a
        if att_uid == _ZERO32:
            result = {"found": False, "note": "no attestation with that uid"}
        else:
            is_numguard = ("0x" + schema.hex()) == schema_uid()
            cred = {}
            if is_numguard and data:
                try:
                    digest, kind, survives, verdict = _decode(SCHEMA_TYPES, data)
                    cred = {"digest": "0x" + digest.hex(), "kind": kind,
                            "survives": bool(survives), "verdict": verdict}
                except Exception:
                    cred = {}
            result = {"found": True, "is_numguard_credential": is_numguard, "revoked": revoked_at != 0,
                      "attester": attester, "recipient": recipient, "attested_at": t, "credential": cred,
                      "easscan": _EASSCAN.get(network, "") + (uid if uid.startswith("0x") else "0x" + uid),
                      "note": ("a genuine numguard verification credential" if is_numguard
                               else "exists but NOT under numguard's schema — not a numguard credential")}
    except Exception:
        return {"error": "read error"}
    with _READ_LOCK:                             # cache the immutable read; bound the cache
        _READ_CACHE[key] = (result, time.time())
        if len(_READ_CACHE) > 5000:
            for k in sorted(_READ_CACHE, key=lambda k: _READ_CACHE[k][1])[:2500]:
                _READ_CACHE.pop(k, None)
    return result


def _selftest():
    d = "ab" * 32
    r = attest_credential(d, "backtest", True, "SR real", dry_run=True)
    assert r["dry_run"] and r["schema_uid"].startswith("0x") and r["encoded_data"].startswith("0x")
    assert schema_uid() == r["schema_uid"]
    assert "error" in attest_credential("short", "k", True, "v")
    print(f"eas selftest: OK (schema_uid={schema_uid()[:14]}…, dry-run encodes, bad-digest rejected)")


if __name__ == "__main__":
    _selftest()
