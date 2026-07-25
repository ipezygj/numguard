"""triage — the front door. An agent describes what it's about to DO or ASSERT; triage routes it to the right
check across the trust-layer (numguard verification + agent-guard safety + evalgate stats), with a one-line
reflex and the exact call. Pure stdlib, deterministic (keyword/intent scoring — no LLM, no network, free).

Why this exists: the primitives are spread across three packages and many tools. A builder/agent shouldn't have
to know which one to reach for — they say "I'm about to publish a backtest Sharpe" or "run this npm package",
and triage answers with the check to run first. It's the canonical entry to the layer: verify a number before
you assert it, check an action before you take it, prove a claim before you're trusted.
"""
from __future__ import annotations
import re

# Each route: (id, product, tool, one-line what, how-to-call, trigger keywords). Scored by keyword hits;
# higher-specificity routes list more/rarer keywords so they win over generic ones.
ROUTES = [
    # ---- VERIFY A NUMBER (numguard) ----
    ("backtest", "numguard", "verify_backtest / verify_backtest_series",
     "Is this backtest Sharpe real or overfit (Deflated Sharpe + the full integrity battery)?",
     "verify_backtest(sr, T, n_trials) — or verify_backtest_series(returns, positions, asset_returns) for look-ahead/PBO",
     ["backtest", "sharpe", "deflated", "overfit", "strategy return", "equity curve", "trading strategy", "pnl", "drawdown", "hyperopt", "grid search"]),
    ("execution", "numguard", "verify_execution",
     "Don't trust the reported Sharpe — RE-DERIVE it from positions on committed price data.",
     "verify_execution(positions, asset_returns, reported_sharpe, canonical_hash=...)",
     ["re-derive", "positions", "reproduce", "recompute", "prove the number", "from the data", "reconstruct pnl"]),
    ("precommit", "numguard", "open_precommitment / report_precommit / verify_chain",
     "Prove a live claim wasn't cherry-picked after the fact — pre-register it BEFORE the outcome.",
     "open_precommitment(strategy_id, claimed_sr, horizon_periods) → report_precommit(...) → verify_chain(pid)",
     ["going live", "track record", "forward", "pre-register", "prove i didn't", "cherry-pick", "backfill", "before the outcome", "committed", "live returns"]),
    ("reconcile", "numguard", "reconcile_backtest",
     "Did a backtest's claimed Sharpe survive contact with LIVE returns? (HELD/DECAYED/BROKEN)",
     "reconcile_backtest(claimed_sr, realized_returns)",
     ["survive live", "held or broken", "decayed", "live vs backtest", "realized returns", "did it hold"]),
    ("model_gap", "evalgate", "verify_model_gap",
     "Is 'model A beats B' real or within the noise for this sample?",
     "verify_model_gap(n, p1, p2)",
     ["model gap", "baseline", "beats", "accuracy gap", "a beats b", "improvement over", "outperform", "better than", "our model"]),
    ("subset_win", "evalgate", "verify_subset_win",
     "Is a 'we lead on subset X' claim real after the look-elsewhere correction?",
     "verify_subset_win(...)",
     ["subset", "we lead", "sota on", "best on", "cherry-picked benchmark", "wins on", "leads on"]),
    ("judge_bias", "evalgate", "verify_judge_bias / calibrate_judge",
     "Is an LLM-judge / metric preference biased (length, position, self-preference)?",
     "verify_judge_bias(...) — or calibrate_judge(judge_caught, truth_caught)",
     ["judge", "llm judge", "preference", "length bias", "position bias", "grader", "rubric", "elo"]),
    ("leaderboard", "evalgate", "audit_leaderboard",
     "Is this leaderboard #1 real or a coin flip? (rank confidence intervals)",
     "audit_leaderboard(results)",
     ["leaderboard", "ranking", "number one", "#1", "top of the board", "benchmark ranking", "arena"]),
    ("claim_generic", "numguard", "verify_claim",
     "A number you're about to assert, kind unclear — route it through the general verifier.",
     "verify_claim(kind, ...)",
     ["verify a number", "is this real", "check this claim", "before i assert", "before i report"]),
    ("receipt", "numguard", "verify_receipt / scan_for_receipts",
     "Someone handed you a number with a receipt — verify it (free, offline) before you trust it.",
     "verify_receipt(receipt) — or scan_for_receipts(message) on an inbound blob",
     ["receipt", "they claim", "peer sent", "someone told me", "trust this number", "verify their", "attestation they"]),
    ("onchain", "numguard", "anchor_receipt / attest_onchain",
     "Make a verified result a permanent, composable on-chain credential (Base / EAS).",
     "anchor_receipt(receipt) — or attest_onchain(receipt, recipient) for a queryable EAS attestation",
     ["on-chain", "onchain", "permanent", "eas", "attest", "credential", "reputation", "composable", "base mainnet", "anchor"]),
    # ---- CHECK AN ACTION (agent-guard) ----
    ("pkg", "agent-guard", "check_package",
     "About to install/import a package — is it malware / a typosquat?",
     "check_package(name, ecosystem)  [pip install agent-tripwire]",
     ["install package", "npm install", "pip install", "add dependency", "import package", "typosquat", "malware", "supply chain", "npm", "package", "dependency", "install this"]),
    ("cmd", "agent-guard", "check_command",
     "About to run a shell command — is it destructive (rm -rf, curl|bash)?",
     "check_command(cmd)  [pip install agent-tripwire]",
     ["shell command", "run command", "rm -rf", "curl | bash", "execute", "bash", "destructive command", "sudo"]),
    ("secret", "agent-guard", "scan_secrets",
     "About to commit/paste text — does it leak an API key / token / private key?",
     "scan_secrets(text)  [pip install agent-tripwire]",
     ["secret", "api key", "credential", "token leak", "private key", "leak", "commit", ".env", "password"]),
    ("webscan", "agent-guard", "scan_project",
     "About to ship AI-generated backend code — fail-open authz, unsigned webhook, SQLi, hardcoded secret?",
     "scan_project(path)  [pip install agent-tripwire]",
     ["web backend", "api endpoint", "deploy code", "authz", "webhook", "sql injection", "sqli", "backend code", "ship code", "ai-generated code", "auth check"]),
]

# Safety intents (an ACTION about to be taken) vs verification intents (a NUMBER about to be asserted).
_SAFETY = {"pkg", "cmd", "secret", "webscan"}


def triage(intent: str, top_k: int = 3) -> dict:
    """Route a free-text intent to the check(s) to run first. Returns the best match + alternatives, each with
    the product, tool, what it does, and the exact call. Deterministic keyword scoring; no LLM, no network."""
    text = " " + re.sub(r"[^a-z0-9 ]+", " ", (intent or "").lower()) + " "
    scored = []
    for rid, product, tool, what, how, kws in ROUTES:
        hits = [k for k in kws if (" " + k + " ") in text or k in text]
        if not hits:
            continue
        # score: number of distinct keyword hits, weighted by keyword length (rarer/longer = more specific)
        score = sum(1 + len(k) / 20.0 for k in hits)
        scored.append((score, rid, product, tool, what, how, hits))
    scored.sort(key=lambda r: -r[0])

    if not scored:
        return {
            "intent": intent,
            "matched": False,
            "reflex": "Not sure which check — tell me what you're about to DO (run/install/commit) or ASSERT "
                      "(a number/claim), and I'll route you.",
            "routes": [],
            "front_door": "numguard is the trust layer: verify a number before you assert it, check an action "
                          "before you take it, prove a claim before you're trusted.",
        }

    def pack(r):
        _, rid, product, tool, what, how, hits = r
        return {"kind": "safety" if rid in _SAFETY else "verification",
                "product": product, "tool": tool, "what": what, "call": how, "matched_on": hits}

    best = scored[0]
    reflex = ("Before you act: " if best[1] in _SAFETY else "Before you assert it: ") + best[4]
    return {
        "intent": intent,
        "matched": True,
        "reflex": reflex,
        "route": pack(best),
        "alternatives": [pack(r) for r in scored[1:top_k]],
        "front_door": "one entry to the trust layer — verify numbers (numguard), check actions (agent-guard), "
                      "audit evals (evalgate); the receipt/attestation is portable across all three.",
    }


def _selftest():
    cases = {
        "I'm about to publish a backtest with a Sharpe of 20": "backtest",
        "should I run this npm install of a package I found": "pkg",
        "about to claim our model beats the baseline on accuracy": "model_gap",
        "a peer sent me a number with a receipt, can I trust it": "receipt",
        "we're going live and I want to prove the track record isn't cherry-picked": "precommit",
        "does this text leak an api key before I commit it": "secret",
        "make this verified result a permanent on-chain credential": "onchain",
        "is our leaderboard #1 real": "leaderboard",
        "about to ship this AI-generated backend with a webhook": "webscan",
    }
    passed, misses = 0, []
    for intent, expect in cases.items():
        r = triage(intent)
        got = _route_id(r["route"]) if r["matched"] else "none"
        if got == expect:
            passed += 1
        else:
            misses.append(f"{intent!r} -> {got} (want {expect})")
    print(f"triage selftest: {passed}/{len(cases)} intents routed correctly")
    for m in misses:
        print("  MISS:", m)
    assert passed >= len(cases) - 1, "triage routing regressed"


def _route_id(route: dict) -> str:
    # map a packed route back to its id by tool string (selftest helper)
    for rid, product, tool, *_ in ROUTES:
        if tool == route["tool"]:
            return rid
    return "?"


if __name__ == "__main__":
    _selftest()
