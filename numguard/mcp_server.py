"""numguard MCP server — the verification layer, callable by any agent.

Tools an agent invokes mid-task to check a number before it asserts it, and to attach a portable, signed
receipt proving it was checked. Every billable tool is metered (generous free tier, then prepaid credits;
pay-per-call over x402 for wallet-native agents). Coordinates with `evalgate` for the shared statistics.

Run:  python -m numguard.mcp_server           (stdio MCP server)
"""
from __future__ import annotations
import json, os
from pathlib import Path

from typing import Annotated, Any, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from . import claims, judge as _judge, backtest as _bt, credits, receipt as _rcpt, _limits
from . import backtest_battery as _battery


class _Out(BaseModel):
    """Base output model — `extra="allow"` so declaring the schema never drops result fields (incl. `_billing`
    or a `payment_required` response); every field optional so no return path can fail validation. Fields are
    untyped (Any) because verdicts mix int/float/bool — the schema documents the shape without risking coercion."""
    model_config = ConfigDict(extra="allow")


class BacktestVerdict(_Out):
    survives: Annotated[Optional[Any], Field(None, description="True if the Sharpe clears the deflated bar.")] = None
    sr: Optional[Any] = None
    dsr: Annotated[Optional[Any], Field(None, description="Deflated Sharpe Ratio (probability it's real).")] = None
    deflation_bar: Optional[Any] = None
    psr_vs_0: Optional[Any] = None
    min_track_record: Optional[Any] = None
    verdict: Annotated[Optional[Any], Field(None, description="One-line human verdict.")] = None


class SubsetWinVerdict(_Out):
    survives: Optional[Any] = None
    raw_p: Optional[Any] = None
    corrected_p: Annotated[Optional[Any], Field(None, description="Multiple-comparisons-corrected p-value.")] = None
    method: Optional[Any] = None
    verdict: Optional[Any] = None


class ModelGapVerdict(_Out):
    survives: Optional[Any] = None
    gap: Optional[Any] = None
    p_value: Optional[Any] = None
    mde: Annotated[Optional[Any], Field(None, description="Minimum detectable effect at this sample size.")] = None
    verdict: Optional[Any] = None


class JudgeBiasVerdict(_Out):
    survives: Optional[Any] = None
    rate: Optional[Any] = None
    p_value: Optional[Any] = None
    verdict: Optional[Any] = None


class JudgeCalibration(_Out):
    n: Optional[Any] = None
    agreement_rate: Optional[Any] = None
    over_credits: Optional[Any] = None
    under_credits: Optional[Any] = None
    biased: Annotated[Optional[Any], Field(None, description="True if the judge's errors lean one direction.")] = None
    direction: Optional[Any] = None
    verdict: Optional[Any] = None


class LeaderboardAudit(_Out):
    verdict: Annotated[Optional[Any], Field(None, description="Whether #1 is statistically real + rank CIs.")] = None


class Receipt(_Out):
    payload: Optional[Any] = None
    digest: Optional[Any] = None
    public_key: Annotated[Optional[Any], Field(None, description="Verify the receipt with only this key.")] = None
    signature: Optional[Any] = None


class Balance(_Out):
    balance: Optional[Any] = None
    free_remaining: Optional[Any] = None
    spent: Optional[Any] = None


class Pricing(_Out):
    unit: Optional[Any] = None
    free_tier_calls: Optional[Any] = None
    prices: Optional[Any] = None
    x402: Optional[Any] = None


class BatteryVerdict(_Out):
    survives: Annotated[Optional[Any], Field(description="True only if NO integrity check flags.")] = None
    risk: Annotated[Optional[Any], Field(description="none / medium / high / critical — the worst flagged "
                                                     "check (look-ahead & overfit = critical).")] = None
    n_checks: Optional[Any] = None
    flags: Annotated[Optional[Any], Field(description="Names of the checks that tripped.")] = None
    checks: Annotated[Optional[Any], Field(description="Per-check results (hac_sharpe, regime_stability, "
                                                       "drawdown, bootstrap_stability, permutation, leakage, "
                                                       "pbo, …).")] = None
    verdict: Optional[Any] = None


def _ann(title: str) -> ToolAnnotations:
    """These tools compute a verdict; they don't mutate the caller's world (metering aside) and are
    deterministic, so they're read-only, non-destructive, idempotent, and closed-world."""
    return ToolAnnotations(title=title, readOnlyHint=True, destructiveHint=False,
                           idempotentHint=True, openWorldHint=False)


# shared parameter description: the metering key every billable tool takes
ApiKey = Annotated[str, Field(description="Your metering key — any stable string identifying you; it tracks "
                                          "your free-tier calls and prepaid credit balance.")]

try:                                   # optional: full leaderboard audit needs the evalgate library
    from evalgate import audit_matrix
except Exception:
    audit_matrix = None

# DNS-rebinding protection guards LOCALHOST servers from malicious web pages; this is a PUBLIC HTTP MCP server
# (reached by arbitrary hosts/proxies, e.g. Render, Smithery), and its tools are pure metered computation with
# no local/privileged access — so the Host/Origin allowlist would only reject legitimate traffic. Off by
# default; set NUMGUARD_MCP_HOSTS (comma-separated) to re-enable a strict allowlist instead.
_hosts = [h.strip() for h in os.environ.get("NUMGUARD_MCP_HOSTS", "").split(",") if h.strip()]
_security = (TransportSecuritySettings(allowed_hosts=_hosts, allowed_origins=_hosts)
             if _hosts else TransportSecuritySettings(enable_dns_rebinding_protection=False))
mcp = FastMCP("numguard", transport_security=_security)

# ---- issuer key for receipts (persistent) ----
_KEYFILE = Path(os.environ.get("NUMGUARD_ISSUER", Path.home() / ".numguard" / "issuer.json"))


def _issuer():
    try:
        d = json.loads(_KEYFILE.read_text(encoding="utf-8"))
        return d["priv"], d["pub"]
    except Exception:
        priv, pub = _rcpt.keypair()
        _KEYFILE.parent.mkdir(parents=True, exist_ok=True)
        _KEYFILE.write_text(json.dumps({"priv": priv, "pub": pub}), encoding="utf-8")
        return priv, pub


def _billed(api_key: str, tool: str, fn):
    """Charge one call; run and attach billing, or return payment_required without running."""
    return credits.gate(api_key, tool, fn)


# --------------------------------------------------------------------------- #
# verification tools
# --------------------------------------------------------------------------- #
@mcp.tool(annotations=_ann("Verify a backtest (Deflated Sharpe Ratio)"))
def verify_backtest(
    api_key: ApiKey,
    sr: Annotated[float, Field(description="Observed per-period Sharpe ratio of the strategy.")],
    T: Annotated[int, Field(description="Sample length — number of return periods in the backtest.")],
    n_trials: Annotated[int, Field(description="How many strategy/parameter variants were tried before "
                                               "reporting this one (the multiple-testing count).")] = 1,
    skew: Annotated[float, Field(description="Skewness of the return series (0 if unknown).")] = 0.0,
    kurt: Annotated[float, Field(description="Kurtosis of the return series (3 = normal).")] = 3.0,
) -> BacktestVerdict:
    """Is a strategy's Sharpe real, or the luckiest of many tried? Deflated Sharpe Ratio: pass the observed
    per-period Sharpe `sr`, sample length `T`, and `n_trials` = how many strategy/parameter variants were
    tested before reporting this one. Optionally the return `skew`/`kurt`. For agent traders verifying
    backtest data before they trust (or publish) it."""
    return _billed(api_key, "verify_backtest",
                   lambda: claims.verify_claim("backtest", sr=sr, T=T, n_trials=n_trials, skew=skew, kurt=kurt))


@mcp.tool(annotations=_ann("Verify a subset/metric win (multiple comparisons)"))
def verify_subset_win(
    api_key: ApiKey,
    p: Annotated[float, Field(description="Raw (uncorrected) p-value of the observed win.")],
    n_tests: Annotated[int, Field(description="Number of subsets/metrics/checkpoints it could have been "
                                              "picked from (the look-elsewhere count).")],
) -> SubsetWinVerdict:
    """A 'we lead on subset/metric/checkpoint X' claim, corrected for how many you could have picked it from
    (look-elsewhere / multiple comparisons). Pass the raw p-value and the number of comparisons tested."""
    return _billed(api_key, "verify_claim", lambda: claims.verify_claim("subset_win", p=p, n_tests=n_tests))


@mcp.tool(annotations=_ann("Verify a model accuracy gap"))
def verify_model_gap(
    api_key: ApiKey,
    n: Annotated[int, Field(description="Number of test items per model.")],
    p1: Annotated[float, Field(description="Accuracy of the first model (0-1).")],
    p2: Annotated[float, Field(description="Accuracy of the second model (0-1).")],
) -> ModelGapVerdict:
    """Is the accuracy gap between two models real, or below what the test set can resolve? Pass items-per-
    model `n` and the two accuracies. Returns the gap, its significance, and the minimum detectable effect."""
    return _billed(api_key, "verify_claim", lambda: claims.verify_claim("model_gap", n=n, p1=p1, p2=p2))


@mcp.tool(annotations=_ann("Verify an LLM-judge / metric preference"))
def verify_judge_bias(
    api_key: ApiKey,
    wins: Annotated[int, Field(description="Number of verdicts the tested side won.")],
    n: Annotated[int, Field(description="Total number of verdicts.")],
    p0: Annotated[float, Field(description="Null win-rate to test against (0.5 = no preference).")] = 0.5,
) -> JudgeBiasVerdict:
    """Is an LLM-judge / metric preference real, or just longer/first/same-family? Pass the count of verdicts
    the tested side won and the total. Exact binomial vs chance."""
    return _billed(api_key, "verify_claim", lambda: claims.verify_claim("judge_bias", wins=wins, n=n, p0=p0))


@mcp.tool(annotations=_ann("Calibrate an LLM judge against ground truth"))
def calibrate_judge(
    api_key: ApiKey,
    judge_caught: Annotated[list, Field(description="The judge's per-item verdicts, as aligned booleans "
                                                    "(True = judge marked it correct/caught).")],
    truth_caught: Annotated[list, Field(description="The known-correct answers, aligned 1:1 with "
                                                    "judge_caught (True = actually correct/caught).")],
) -> JudgeCalibration:
    """Check an LLM judge against ground truth on a labelled slice. Pass aligned booleans: the judge's
    verdicts and the known-correct answers. Returns agreement and whether the judge's errors lean one
    direction (over-crediting = the length/self-preference failure mode)."""
    try:
        _limits.check_list("judge_caught", judge_caught)
        _limits.check_list("truth_caught", truth_caught)
    except ValueError as e:
        return {"error": f"bad request: {e}"}          # reject oversized input before billing
    return _billed(api_key, "calibrate_judge", lambda: _judge.calibrate_judge(judge_caught, truth_caught))


@mcp.tool(annotations=_ann("Audit a leaderboard (rank confidence)"))
def audit_leaderboard(
    api_key: ApiKey,
    results: Annotated[dict, Field(description="Per-model results: each model name maps to the list of "
                                              "item-ids it solved, or a {item: score} dict.")],
    n_boot: Annotated[int, Field(description="Bootstrap iterations for the rank confidence intervals.")] = 1000,
) -> LeaderboardAudit:
    """Audit a whole leaderboard from per-item results. `results` maps each model to the list of item-ids it
    solved (or a {item: score} dict). Returns rank confidence intervals + whether #1 is statistically real."""
    try:
        _limits.check_leaderboard(results, n_boot)
    except ValueError as e:
        return {"error": f"bad request: {e}"}          # reject oversized input before billing
    def run():
        if audit_matrix is None:
            return {"error": "leaderboard audit needs the evalgate library: pip install "
                             "git+https://github.com/ipezygj/evalgate"}
        a = audit_matrix(results, n_boot=n_boot, seed=0)
        try:
            from dataclasses import asdict
            d = asdict(a)
        except Exception:
            d = {"summary": str(a)}
        d["verdict"] = str(a)
        return d
    return _billed(api_key, "audit_leaderboard", run)


@mcp.tool(annotations=_ann("Verify a claim and issue a signed receipt"))
def issue_receipt(
    api_key: ApiKey,
    kind: Annotated[str, Field(description="Claim type to verify + attest: 'backtest', 'subset_win', "
                                           "'model_gap', or 'judge_bias'.")],
    inputs: Annotated[dict, Field(description="Inputs for that claim — the same params as the matching "
                                             "verify_* tool, e.g. {'sr':0.12,'T':250,'n_trials':100} for backtest.")],
) -> Receipt:
    """Verify a claim server-side and hand back a portable, signed receipt (Ed25519) of numguard's OWN verdict.
    numguard **recomputes** the verdict from your inputs — it never signs a result you supply — so the receipt
    is real proof the claim was checked, not just an assertion. Anyone can verify it with only the public key."""
    priv, pub = _issuer()

    def run():
        verdict = claims.verify_claim(kind, **(inputs or {}))   # numguard computes it — the client can't forge it
        return _rcpt.issue_receipt(verdict, priv, pub)

    return _billed(api_key, "issue_receipt", run)


@mcp.tool(annotations=_ann("Verify an agent action-trace was guarded, issue a receipt"))
def verify_guard_trace(
    api_key: ApiKey,
    trace: Annotated[list, Field(description="The agent's ordered action list — each item a dict like "
                                             "{'kind':'command','command':'curl ...'} / {'kind':'file_read',"
                                             "'path':'~/.ssh/id_rsa'} / {'kind':'fetch','url':'https://...'} / "
                                             "{'kind':'untrusted','value':'source'}.")],
    task: Annotated[str, Field(description="The user's original task (lets off-task actions be surfaced).")] = "",
) -> Receipt:
    """Recompute the BEHAVIOURAL-guard verdict over an agent's action-trace and hand back a portable,
    signed receipt (Ed25519) — proof the run was guarded and what the guard decided. Catches what a
    code scanner can't: a cross-call exfiltration chain (read a secret → later send it to a non-
    allowlisted host) or an action taken right after ingesting untrusted content (prompt-injection
    consequence). numguard **recomputes** the verdict from the trace — it never signs a verdict you
    supply — so the receipt is real evidence, verifiable by anyone with only the public key.

    Use when: an agent needs to PROVE a run passed the behavioural guard (compliance, audit, handing
    verified work to another party)."""
    try:
        from agent_guard.session import evaluate_sequence
        from agent_guard import receipt as _ag_rcpt
    except Exception:
        return {"payload": None, "signature": None,
                "verdict": "agent-guard engine not installed on this server. Install `numguard[guard]` "
                           "(pulls agent-tripwire) to enable guard-trace receipts."}
    priv, pub = _issuer()

    def run():
        # numguard recomputes the verdict itself (client can't forge it), then signs the guard-shaped
        # payload with numguard's issuer key. agent-guard's receipt scheme is identical to numguard's,
        # so this receipt verifies with numguard.verify_receipt / _rcpt.verify_receipt unchanged.
        verdict = evaluate_sequence(trace or [], task=task)
        return _ag_rcpt.issue_receipt(verdict, priv, pub)

    return _billed(api_key, "verify_guard_trace", run)


@mcp.tool(annotations=_ann("Full backtest-integrity battery on the returns series"))
def verify_backtest_series(
    api_key: ApiKey,
    returns: Annotated[list, Field(description="The strategy's per-period return series (the actual numbers, "
                                              "not a summary).")],
    positions: Annotated[Optional[list], Field(description="Optional aligned position/signal series — enables "
                                                          "the same-bar look-ahead (leakage) check.")] = None,
    asset_returns: Annotated[Optional[list], Field(description="Optional aligned underlying-asset returns — "
                                                              "needed with `positions` for the leakage check.")] = None,
    turnover: Annotated[Optional[list], Field(description="Optional per-period turnover — enables the "
                                                         "breakeven-cost check.")] = None,
    candidates: Annotated[Optional[list], Field(description="Optional matrix (rows = the candidate strategies "
                                                           "you picked the winner from) — enables PBO.")] = None,
    periods_per_year: Annotated[int, Field(description="Periods per year for annualization (252 daily).")] = 252,
) -> BatteryVerdict:
    """Run the checks a Deflated-Sharpe pass STILL misses — on the actual returns series. Catches same-bar
    look-ahead, autocorrelation-inflated Sharpe (HAC), regime dependence / cherry-picked windows, drawdown &
    tail fantasy, one-lucky-epoch fragility (block bootstrap), overfitting beyond n_trials (PBO), and
    volatility-clustering. Returns a combined verdict + the checks that flagged. Pass `positions`+`asset_returns`
    for the leakage check, `turnover` for cost, `candidates` (a matrix) for PBO."""
    _limits.check_list("returns", returns)
    for nm, v in (("positions", positions), ("asset_returns", asset_returns), ("turnover", turnover)):
        if v is not None:
            _limits.check_list(nm, v)
    if candidates is not None:
        if not isinstance(candidates, list) or sum(len(r) for r in candidates if hasattr(r, "__len__")) > _limits.MAX_ITEMS:
            raise ValueError("candidates too large")
    return _billed(api_key, "verify_backtest_series",
                   lambda: _battery.run_battery(returns, positions=positions, asset_returns=asset_returns,
                                                turnover=turnover, candidates=candidates,
                                                periods_per_year=periods_per_year))


@mcp.tool(annotations=_ann("Check your free calls + credit balance"))
def balance(api_key: ApiKey) -> Balance:
    """Your remaining free calls and prepaid credit balance."""
    return credits.balance(api_key)


@mcp.tool(annotations=_ann("List prices + free tier"))
def pricing() -> Pricing:
    """Machine-readable price list (credits; 1 credit = $0.01) and the free-tier size, so an agent can decide
    before it calls. Also returns the wallet-native x402 rail an agent can pay at once its free tier is used."""
    return {"unit": "credit ($0.01)", "free_tier_calls": credits.FREE_TIER_CALLS,
            "prices": credits.PRICES,
            "x402": {"how": "wallet-native pay-per-call (USDC on Base), no signup",
                     "pricing": f"{credits.PUBLIC_URL}/pricing", "base_url": credits.PUBLIC_URL}}


# ASGI app for remote MCP hosts / deployment:  uvicorn numguard.mcp_server:app
def _http_app():
    return mcp.streamable_http_app()


if __name__ == "__main__":
    # NUMGUARD_TRANSPORT = stdio (default, for local MCP hosts) | streamable-http | sse
    transport = os.environ.get("NUMGUARD_TRANSPORT", "stdio")
    mcp.run(transport=transport)
