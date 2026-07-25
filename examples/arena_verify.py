"""Verify a public trading-agent track record end-to-end → a signed receipt → an ERC-8004 reputation signal.

Built for the Virtuals-Arena shape: agents COMPETE, and their Sharpe + trades are public on-chain (Base). Because
the trades are public, this is the rare case where the number can be RE-DERIVED from operator-independent data
instead of trusted — closing the self-report gap. The pipeline:

  1. re-derive the Sharpe from the agent's positions applied to the (public) asset returns   [verify_execution]
  2. DEFLATE it for the competition — the winner of an N-agent arena is a max over N          [deflated_sharpe]
  3. issue a portable, signed receipt of the verdict                                          [issue_receipt]
  4. build the exact ERC-8004 giveFeedback calldata to post it as the agent's reputation      [erc8004]

    python examples/arena_verify.py

DATA SOURCE — HONEST: the series below is ILLUSTRATIVE (clearly labeled), so this runs offline as a demo of the
METHOD, not a claim about any real agent. To make it a REAL receipt, replace `positions`/`asset_returns` with a
specific agent's public track record (from the Virtuals API or its on-chain fills on Base) and set `agent_id` to
its ERC-8004 identity. The code path is identical; only the input changes.
"""
import random
import sys

from numguard import execute as ex, backtest as bt, issue_receipt, keypair, erc8004


def load_agent_series():
    """REPLACE THIS with a real Arena agent's public track record (positions + aligned asset returns).
    Returns (positions, asset_returns, arena_size, reported_annual_sharpe). Illustrative values for the demo."""
    rng = random.Random(20260505)                       # seeded == deterministic demo, NOT a real agent
    asset_returns = [rng.gauss(0.0007, 0.02) for _ in range(365)]     # ~1y of daily moves the agent traded on
    positions = [0.0] + [(1.0 if asset_returns[t - 1] > 0 else -1.0) for t in range(1, 365)]  # a momentum agent
    arena_size = 64                                     # agents it was the best-of in the competition
    reported = bt.sharpe(ex.reprice(positions, asset_returns)) * (252 ** 0.5)  # what the app would display
    return positions, asset_returns, arena_size, reported


def verify_real(address: str):
    """REAL mode: pass a wallet address and numguard fetches its public on-chain trades and verifies them.
    `python examples/arena_verify.py 0x<address>` — no key needed (keyless via Blockscout on Base)."""
    from numguard import onchain as oc
    r = oc.verify_agent(address, agent_id=0, chain="base", arena_size=32)
    print(f"== REAL on-chain verification of {address} ==")
    for k in ("round_trips", "realized_sharpe", "deflated_sharpe", "survives", "verdict", "receipt_digest"):
        if k in r:
            print(f"  {k}: {r[k]}")
    print("\n(fetched + re-derived from the wallet's actual public trades — operator-independent, no self-report)")


def main():
    if len(sys.argv) > 1 and sys.argv[1].startswith("0x"):
        return verify_real(sys.argv[1])
    positions, asset_returns, arena_size, reported = load_agent_series()
    agent_id = 1734                                     # e.g. an ERC-8004 agentId (illustrative)
    print("== Verifying a trading-agent track record (ILLUSTRATIVE data — the method, not a real agent) ==\n")

    # 1) re-derive the Sharpe from decisions on the (public) price series, and check it matches the reported one,
    #    deflating for the arena's N competitors in one shot (n_trials = arena_size).
    v = ex.verify_execution(positions, asset_returns, reported_sharpe=reported,
                            n_trials=arena_size, periods_per_year=252)
    print(f"reported (annualized) : {reported:+.2f}")
    print(f"re-derived from trades : {v['recomputed_sharpe_annualized']:+.2f}   matches={v['matches_reported']}")
    print(f"deflated for {arena_size}-agent arena: DSR={v['deflated_sharpe']:.3f}  survives={v['deflated_survives']}")
    print(f"bound to data hash     : {v['data_hash'][:20]}…\n")

    # 2) sign a portable receipt of the verdict (survives = re-derived AND clears the competition bar)
    survives = bool(v["matches_reported"]) and bool(v["deflated_survives"])
    priv, pub = keypair()
    receipt = issue_receipt({
        "kind": "arena_track_record", "survives": survives,
        "verdict": f"re-derived Sharpe {v['recomputed_sharpe_annualized']:+.2f}, "
                   f"DSR {v['deflated_sharpe']:.2f} over {arena_size} agents",
        "data_source": "public_onchain" if False else "illustrative",   # set public_onchain on real data
    }, priv, pub)
    print(f"signed receipt digest  : {receipt['digest'][:20]}…  (verifiable offline with the public key)")

    # 3) the exact ERC-8004 Reputation Registry post for this agent (dry-run calldata, ready to broadcast)
    fb = erc8004.post_feedback(receipt, agent_id, network="base", dry_run=True)
    print(f"ERC-8004 giveFeedback  : value={fb['args']['value']} tag2={fb['args']['tag2']!r} "
          f"-> registry {fb['registry'][:12]}…  (calldata {len(fb['calldata'])} hex)")
    print("\nSwap the illustrative series for a real agent's public track record and this becomes a real, "
          "on-chain-postable reputation signal — no self-report, because the trades are public.")


if __name__ == "__main__":
    main()
