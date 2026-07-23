"""credits — prepaid, per-call billing an agent can actually use.

The easiest agent-native rail: a human tops up a balance once, the agent spends per call with an API key —
zero per-call friction. Each tool has a machine-readable price so an agent can decide before it calls.
Insufficient balance returns a structured 'payment required' object (not an exception) with the price and how
to top up, so the agent can react in-loop.

JSON-file ledger (swap for a DB in production). Charges are recorded so every spend is auditable.
"""
from __future__ import annotations
import json, os, time
from pathlib import Path

# per-call price in credits (1 credit = $0.01 by convention; set your own). Verification is cheap; the value
# is running ALL of it, on a number the agent is too close to. Generous free tier below drives adoption.
PRICES = {
    "verify_claim": 2,        # ~$0.02
    "calibrate_judge": 5,
    "audit_leaderboard": 5,
    "verify_backtest": 3,
    "issue_receipt": 1,       # cheap add-on; the receipt is the sticky part
}
FREE_TIER_CALLS = 25          # per key, before any charge — let the agent feel the value first

_STORE = Path(os.environ.get("NUMGUARD_LEDGER", Path.home() / ".numguard" / "ledger.json"))


def _load() -> dict:
    try:
        return json.loads(_STORE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d: dict) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    _STORE.write_text(json.dumps(d, indent=1), encoding="utf-8")


def _acct(d: dict, key: str) -> dict:
    return d.setdefault(key, {"balance": 0, "free_used": 0, "spent": 0, "history": []})


def price_of(tool: str) -> int:
    return PRICES.get(tool, 2)


def balance(api_key: str) -> dict:
    a = _acct(_load(), api_key)
    return {"balance": a["balance"], "free_remaining": max(0, FREE_TIER_CALLS - a["free_used"]),
            "spent": a["spent"]}


def topup(api_key: str, credits: int) -> dict:
    d = _load(); a = _acct(d, api_key); a["balance"] += int(credits)
    a["history"].append({"t": int(time.time()), "type": "topup", "credits": int(credits)})
    _save(d); return {"api_key": api_key[:6] + "…", "balance": a["balance"]}


def charge(api_key: str, tool: str) -> dict:
    """Charge one call. Uses the free tier first, then the balance. Returns {ok, ...} or a payment_required."""
    cost = price_of(tool)
    d = _load(); a = _acct(d, api_key)
    if a["free_used"] < FREE_TIER_CALLS:
        a["free_used"] += 1
        a["history"].append({"t": int(time.time()), "type": "free", "tool": tool})
        _save(d)
        return {"ok": True, "charged": 0, "free_remaining": FREE_TIER_CALLS - a["free_used"]}
    if a["balance"] >= cost:
        a["balance"] -= cost; a["spent"] += cost
        a["history"].append({"t": int(time.time()), "type": "charge", "tool": tool, "credits": cost})
        _save(d)
        return {"ok": True, "charged": cost, "balance": a["balance"]}
    return {"ok": False, "payment_required": True, "tool": tool, "price": cost,
            "balance": a["balance"], "unit": "credit ($0.01)",
            "message": f"Insufficient credits for {tool} (needs {cost}, have {a['balance']}). "
                       f"Top up, or pay per-call via x402 (see numguard.x402)."}


def gate(api_key: str, tool: str, fn, *args, **kwargs) -> dict:
    """Charge then run. On success embeds the billing in the result; on empty balance returns payment_required
    WITHOUT running the tool — the agent can top up and retry."""
    ch = charge(api_key, tool)
    if not ch.get("ok"):
        return ch
    out = fn(*args, **kwargs)
    if isinstance(out, dict):
        out = {**out, "_billing": {"charged": ch.get("charged", 0), "tool": tool}}
    return out


def _selftest():
    import tempfile
    global _STORE
    _STORE = Path(tempfile.mkdtemp()) / "ledger.json"
    k = "test_key_abc"
    # free tier first
    for _ in range(FREE_TIER_CALLS):
        assert charge(k, "verify_claim")["ok"]
    # now empty -> payment required
    r = charge(k, "verify_claim")
    assert not r["ok"] and r["payment_required"]
    # topup restores
    topup(k, 10)
    r2 = charge(k, "verify_claim")
    assert r2["ok"] and r2["charged"] == PRICES["verify_claim"]
    print("credits selftest: OK (free tier -> payment_required -> topup -> charge)")


if __name__ == "__main__":
    _selftest()
