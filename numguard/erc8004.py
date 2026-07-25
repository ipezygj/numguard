"""erc8004 — plug numguard into the ERC-8004 "Trustless Agents" standard as a REPUTATION provider.

ERC-8004 is live on 40+ chains (incl. Base) with a stable Reputation Registry: `giveFeedback(...)` posts a
signed numeric signal + tags + an off-chain payload URI+hash ABOUT an agent. That is EXACTLY the shape of a
numguard verdict — so a numguard receipt maps straight onto a reputation feedback signal, making a verified
claim portable, composable on-chain reputation in the standard agents are adopting. This is "join the infra,"
not build a competing app: distribution via the standard, not cold BD.

This module MAPS a numguard receipt → the giveFeedback arguments (pure, testable, no chain writes). The actual
broadcast is a documented step: call the chain's Reputation Registry with these args (needs the registry
address, gas, and the subject agentId). The Validation Registry is still experimental/under active update, so
we target the stable Reputation Registry only for now.

HONEST SCOPE: this attests numguard's CHECK of a claim (proof-of-check / self_reported data source, per the
receipt) — it is a reputation *signal*, not a settlement of truth. Reading agents weigh it like any signal.
"""
from __future__ import annotations

# map a verdict to a small signed reputation value; the full verdict travels in tags + the linked receipt.
def _value_for(claim: dict):
    s = claim.get("survives")
    if s is True:
        return 1, ["numguard", "verified", str(claim.get("kind", "claim"))]
    if s is False:
        return -1, ["numguard", "flagged", str(claim.get("kind", "claim"))]
    return 0, ["numguard", "pending", str(claim.get("kind", "claim"))]


def to_feedback(receipt: dict, agent_id, uri_base: str = "") -> dict:
    """Map a numguard vcr/1 receipt onto ERC-8004 Reputation Registry `giveFeedback` arguments, ABOUT `agent_id`.

    Returns the call shape:
      { agent_id, value (int128), valueDecimals (uint8), tags, uri, hash (0x…32 bytes), function }
    `value` = +1 verified / -1 flagged / 0 pending; the human verdict + kind ride in `tags`; the FULL receipt is
    linked by `uri` (+ `hash` = the receipt digest) so any reader can fetch and independently verify it (free).
    """
    if not isinstance(receipt, dict) or "payload" not in receipt or "digest" not in receipt:
        raise ValueError("need a numguard vcr/1 receipt (payload + digest)")
    claim = (receipt.get("payload") or {}).get("claim", {}) or {}
    value, tags = _value_for(claim)
    verdict = claim.get("verdict")
    if verdict:
        tags = tags + [str(verdict)[:32]]
    digest = receipt["digest"]
    uri = (uri_base.rstrip("/") + "/receipts/" + digest) if uri_base else ("numguard:receipt/" + digest)
    return {
        "function": "giveFeedback",
        "registry": "ERC-8004 Reputation Registry (per-chain address; live on Base + 40 others)",
        "agent_id": agent_id,                       # the subject agent being rated
        "value": value,                             # int128 signed reputation signal
        "valueDecimals": 0,                         # integer signal
        "tags": tags[:8],
        "uri": uri,                                 # where the full receipt lives (independently verifiable)
        "hash": "0x" + digest,                      # bytes32 of the receipt (tamper-evident link)
        "data_source": (receipt.get("payload") or {}).get("data_source", "proof_of_check"),
        "note": "post these to the chain's Reputation Registry giveFeedback (needs registry address + gas). "
                "Self-feedback is blocked; a third party (numguard/its user) posts about the agent. The linked "
                "receipt is offline-verifiable with only its public key — the on-chain signal is a pointer, the "
                "proof is the receipt.",
    }


def _selftest():
    from . import issue_receipt, keypair
    priv, pub = keypair()
    good = issue_receipt({"kind": "backtest", "survives": True, "verdict": "DSR 1.0 survives"}, priv, pub)
    bad = issue_receipt({"kind": "backtest", "survives": False, "verdict": "overfit — DSR 0.04"}, priv, pub)
    fg = to_feedback(good, agent_id=42, uri_base="https://numguard-4x7u.onrender.com")
    fb = to_feedback(bad, agent_id=42)
    assert fg["value"] == 1 and "verified" in fg["tags"] and fg["hash"] == "0x" + good["digest"]
    assert fg["uri"].endswith(good["digest"]) and fg["uri"].startswith("https://")
    assert fb["value"] == -1 and "flagged" in fb["tags"]
    assert any("overfit" in t for t in fb["tags"])
    try:
        to_feedback({"nope": 1}, agent_id=1); assert False
    except ValueError:
        pass
    print(f"erc8004 selftest: OK (verified->+1 tags={fg['tags']}, flagged->-1, receipt linked by uri+hash)")


if __name__ == "__main__":
    _selftest()
