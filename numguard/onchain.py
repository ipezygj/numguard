"""onchain — auto-fetch an agent's PUBLIC on-chain trades and turn them into a verifiable return series.

This is the missing rail: for an on-chain trading agent, the data is public, so numguard should FETCH it itself
(given a wallet address) rather than trust a hand-assembled series. That's what makes the verification
operator-independent AND one-call: address -> fetch -> re-derive -> receipt -> ERC-8004 reputation.

Self-contained reconstruction (NO external price oracle): a DEX swap moves two tokens in one tx, so the
EXECUTED price is in the swap itself (amount_out / amount_in). We pair the wallet's token transfers by tx into
swaps, FIFO-match buys->sells per token, and read the REALIZED return of each round-trip straight off the chain.

DATA SOURCE: the Etherscan-v2 unified API (chainid 8453 = Base) with a FREE key in NUMGUARD_ETHERSCAN_KEY (or
ETHERSCAN_API_KEY). Public RPCs are 403/range-capped/unreliable for this, so a key is required for live fetch —
the RECONSTRUCTION logic below is pure and unit-tested without one. Robust perps/margin + open-position
mark-to-market is a deeper build; this handles the spot round-trip case correctly and honestly.
"""
from __future__ import annotations
import json
import os
import urllib.request

_ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"
_CHAIN_IDS = {"base": 8453, "base-sepolia": 84532}
# Blockscout: an open, KEYLESS, Etherscan-compatible explorer API — the default for Base (free Etherscan keys do
# NOT cover Base on the v2 API). So numguard fetches Base on-chain data with no key at all.
_BLOCKSCOUT = {"base": "https://base.blockscout.com", "base-sepolia": "https://base-sepolia.blockscout.com"}


import re as _re
_ADDR = _re.compile(r"^0x[0-9a-fA-F]{40}$")


def valid_address(a: str) -> bool:
    """A 0x-prefixed 20-byte hex address. Gate BEFORE putting a value into a fetch URL — blocks injection /
    SSRF-style tricks and garbage that would waste a fetch."""
    return isinstance(a, str) and bool(_ADDR.match(a.strip()))


def _key() -> str:
    return os.environ.get("NUMGUARD_ETHERSCAN_KEY") or os.environ.get("ETHERSCAN_API_KEY", "")


_MAX_RESP = 16 * 1024 * 1024   # 16MB cap on any explorer response (DoS guard)


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 numguard/onchain"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read(_MAX_RESP + 1)
    if len(raw) > _MAX_RESP:
        raise RuntimeError("explorer response too large")
    return json.loads(raw)


def fetch_token_transfers(address: str, chain: str = "base", key: str = "", page_size: int = 1000) -> list:
    """Fetch the wallet's ERC-20 transfers on `chain`. Default source is Blockscout (KEYLESS, covers Base). If an
    Etherscan key is provided it's used as a fallback / for chains Blockscout doesn't serve. Returns raw rows."""
    if not valid_address(address):
        raise RuntimeError("invalid wallet address (expected 0x + 40 hex)")
    page_size = max(1, min(int(page_size), 5000))   # cap the fetch/parse size (DoS guard)
    bs = _BLOCKSCOUT.get(chain)
    if bs:
        try:
            data = _get(f"{bs}/api?module=account&action=tokentx&address={address}&page=1&offset={page_size}&sort=asc")
            if isinstance(data.get("result"), list):
                return data["result"]
        except Exception:
            pass   # fall through to Etherscan if Blockscout is down
    key = key or _key()
    if not key:
        raise RuntimeError(f"could not fetch {chain} transfers (Blockscout unavailable and no Etherscan key set)")
    cid = _CHAIN_IDS.get(chain, 8453)
    data = _get(f"{_ETHERSCAN_V2}?chainid={cid}&module=account&action=tokentx&address={address}"
                f"&page=1&offset={page_size}&sort=asc&apikey={key}")
    if str(data.get("status")) != "1" and not isinstance(data.get("result"), list):
        raise RuntimeError(f"etherscan: {data.get('message')} {str(data.get('result'))[:120]}")
    return data.get("result", [])


def parse_swaps(transfers: list, wallet: str) -> list:
    """Pair token transfers by tx hash into swaps for `wallet`: a swap = one token OUT and one token IN in the
    same tx. Returns [{tx, token_in, qty_in, token_out, qty_out, price_in_per_out}] in time order."""
    w = wallet.lower()
    by_tx: dict = {}
    for t in transfers:
        h = t.get("hash")
        by_tx.setdefault(h, {"ts": int(t.get("timeStamp", 0)), "in": [], "out": []})
        frm, to = (t.get("from") or "").lower(), (t.get("to") or "").lower()
        dec = int(t.get("tokenDecimal") or 18)
        try:
            amt = int(t.get("value", "0")) / (10 ** dec)
        except Exception:
            continue
        sym = t.get("tokenSymbol") or (t.get("contractAddress") or "")[:10]
        if to == w:
            by_tx[h]["in"].append((sym, amt))
        elif frm == w:
            by_tx[h]["out"].append((sym, amt))
    swaps = []
    for h, v in sorted(by_tx.items(), key=lambda kv: kv[1]["ts"]):
        if len(v["in"]) == 1 and len(v["out"]) == 1:      # clean 1:1 swap
            (ti, qi), (to_, qo) = v["in"][0], v["out"][0]
            if qi > 0 and qo > 0:
                swaps.append({"tx": h, "ts": v["ts"], "token_in": ti, "qty_in": qi,
                              "token_out": to_, "qty_out": qo, "price": qo / qi})  # units of token_out per token_in
    return swaps


# Stable/quote symbols treated as "cash" — a swap OUT of cash INTO a token = a BUY; the reverse = a SELL.
# Covers the major Base pairing assets so an agent's round-trips are captured whatever it quotes against.
_QUOTES = {"USDC", "USDT", "DAI", "USDBC", "USDBC", "EURC", "WETH", "ETH", "CBETH", "CBBTC", "VIRTUAL", "AERO"}


def realized_returns(swaps: list, quote: str = "") -> list:
    """FIFO-match buys->sells per traded token and return the list of per-round-trip realized returns (sell
    proceeds / buy cost - 1). Self-contained: prices come from the swaps themselves. `quote` overrides the cash
    symbol set (else any of USDC/USDT/DAI/WETH/ETH counts as cash)."""
    quotes = {quote.upper()} if quote else _QUOTES
    lots: dict = {}   # token -> list of (qty_remaining, unit_cost_in_quote)
    rets = []
    # token_in = what enters the wallet, token_out = what leaves it. BUY a token = cash leaves, token enters
    # (token_out is cash). SELL = token leaves, cash enters (token_in is cash).
    for s in swaps:
        ti, to_ = s["token_in"].upper(), s["token_out"].upper()
        if to_ in quotes and ti not in quotes:            # BUY `ti` with cash: qty_out(cash) -> qty_in(ti)
            unit_cost = s["qty_out"] / s["qty_in"]         # cash per unit token bought
            lots.setdefault(ti, []).append((s["qty_in"], unit_cost))
        elif ti in quotes and to_ not in quotes:          # SELL `to_` for cash: qty_out(to_) -> qty_in(cash)
            proceeds_per = s["qty_in"] / s["qty_out"]      # cash per unit token sold
            qty, lot = s["qty_out"], lots.get(to_, [])
            while qty > 1e-18 and lot:
                lqty, lcost = lot[0]
                take = min(qty, lqty)
                if lcost > 0:
                    rets.append(proceeds_per / lcost - 1.0)
                lqty -= take; qty -= take
                if lqty <= 1e-18:
                    lot.pop(0)
                else:
                    lot[0] = (lqty, lcost)
            lots[to_] = lot
    return rets


def track_record_metrics(rets: list) -> dict:
    """The full public track-record metrics agents advertise — re-derived from the realized round-trips (win
    rate, profit factor, max drawdown, Calmar). All from the same on-chain data, no self-report."""
    n = len(rets)
    if n == 0:
        return {}
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    # equity curve (compounded) → max drawdown
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= (1.0 + r)
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak if peak > 0 else 0.0)
    total_return = eq - 1.0
    return {
        "round_trips": n,
        "win_rate": round(len(wins) / n, 4),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "avg_return": round(sum(rets) / n, 4),
        "total_return": round(total_return, 4),
        "max_drawdown": round(mdd, 4),
        "calmar": round(total_return / mdd, 3) if mdd > 0 else None,
        "best": round(max(rets), 4), "worst": round(min(rets), 4),
    }


def verify_agent(address: str, agent_id: int = 0, *, chain: str = "base", arena_size: int = 1,
                 key: str = "") -> dict:
    """ONE CALL: fetch the agent's public trades -> realized returns -> Deflated-Sharpe (deflated for an
    arena_size-agent field) -> a signed receipt -> the ERC-8004 giveFeedback shape. The wired path. Needs a free
    Etherscan key for the live fetch; raises a clear error without one."""
    from . import backtest as _bt, execute as _ex, erc8004 as _erc
    from . import issue_receipt as _issue
    transfers = fetch_token_transfers(address, chain=chain, key=key)
    swaps = parse_swaps(transfers, address)
    rets = realized_returns(swaps)
    out = {"address": address, "chain": chain, "trades_paired": len(swaps), "round_trips": len(rets)}
    if len(rets) < 8:
        out["verdict"] = f"too few realized round-trips ({len(rets)}) to judge — need >= 8"
        out["survives"] = None
        return out
    # DATA-QUALITY GATE (measured, not believed — applied to our own reconstruction). Absurd round-trips
    # (>1000% or ~total loss) are almost always artifacts: token-decimal mismatches, memecoin price ratios,
    # scam-token swaps, or FIFO mispairing — NOT real spot returns. If the series is contaminated, REFUSE to
    # report a Sharpe rather than sign a number built on garbage.
    outliers = [r for r in rets if r > 10.0 or r <= -0.999]
    if len(outliers) > max(1, 0.05 * len(rets)):
        out.update({"survives": None, "data_source": "public_onchain", "reconstruction": "unreliable",
                    "outlier_round_trips": len(outliers),
                    "verdict": f"reconstruction UNRELIABLE — {len(outliers)}/{len(rets)} round-trips show extreme "
                               f"returns (>1000% or total loss), i.e. token-decimal/memecoin/parsing artifacts, "
                               f"not clean spot trades. Won't sign a Sharpe on contaminated data; needs the "
                               f"agent's own accounting or a cleaner venue."})
        return out
    per = _bt.sharpe(rets)
    v = _bt.deflated_sharpe(sr=per, T=len(rets), n_trials=max(1, arena_size))
    out.update({"realized_sharpe": round(per, 4), "deflated_sharpe": round(v.dsr, 4),
                "survives": bool(v.survives), "data_source": "public_onchain",
                "metrics": track_record_metrics(rets),   # the FULL public track record, re-derived on-chain
                "verdict": f"realized SR {per:+.3f} over {len(rets)} on-chain round-trips; DSR {v.dsr:.3f} "
                           f"(deflated for {arena_size} agents)"})
    from numguard import keypair as _kp
    priv, pub = _kp()
    rc = _issue({"kind": "onchain_track_record", "survives": out["survives"], "verdict": out["verdict"],
                 "data_source": "public_onchain"}, priv, pub)
    out["receipt_digest"] = rc["digest"]
    if agent_id:
        out["erc8004"] = _erc.post_feedback(rc, agent_id, network=chain if chain in _erc.REPUTATION_REGISTRY else "base",
                                            dry_run=True)
    return out


def _selftest():
    # synthetic swaps: buy TOKEN @1.0 (x2 lots), sell half @1.5 (+50%), sell rest @0.8 (-20%) — logic tested
    # WITHOUT any live key, proving the reconstruction is correct.
    transfers = []
    def xfer(h, ts, frm, to, sym, val, dec=18):
        transfers.append({"hash": h, "timeStamp": str(ts), "from": frm, "to": to, "tokenSymbol": sym,
                          "value": str(int(val * 10 ** dec)), "tokenDecimal": str(dec)})
    W = "0xabc"; U = "0xother"
    xfer("h1", 1, W, U, "USDC", 100); xfer("h1", 1, U, W, "TKN", 100)   # buy 100 TKN @ 1.0
    xfer("h2", 2, W, U, "USDC", 100); xfer("h2", 2, U, W, "TKN", 100)   # buy 100 TKN @ 1.0
    xfer("h3", 3, W, U, "TKN", 100);  xfer("h3", 3, U, W, "USDC", 150)  # sell 100 TKN @ 1.5 -> +50%
    xfer("h4", 4, W, U, "TKN", 100);  xfer("h4", 4, U, W, "USDC", 80)   # sell 100 TKN @ 0.8 -> -20%
    swaps = parse_swaps(transfers, W)
    assert len(swaps) == 4, swaps
    rets = realized_returns(swaps)
    assert len(rets) == 2 and abs(rets[0] - 0.5) < 1e-9 and abs(rets[1] + 0.2) < 1e-9, rets
    print(f"onchain selftest: OK (paired {len(swaps)} swaps, FIFO realized returns {['%+.0f%%'%(r*100) for r in rets]}; "
          f"live Base fetch is keyless via Blockscout)")


if __name__ == "__main__":
    _selftest()
