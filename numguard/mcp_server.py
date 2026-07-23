"""numguard MCP server — the verification layer, callable by any agent.

Tools an agent invokes mid-task to check a number before it asserts it, and to attach a portable, signed
receipt proving it was checked. Every billable tool is metered (generous free tier, then prepaid credits;
pay-per-call over x402 for wallet-native agents). Coordinates with `evalgate` for the shared statistics.

Run:  python -m numguard.mcp_server           (stdio MCP server)
"""
from __future__ import annotations
import json, os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import claims, judge as _judge, backtest as _bt, credits, receipt as _rcpt, _limits

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
@mcp.tool()
def verify_backtest(api_key: str, sr: float, T: int, n_trials: int = 1,
                    skew: float = 0.0, kurt: float = 3.0) -> dict:
    """Is a strategy's Sharpe real, or the luckiest of many tried? Deflated Sharpe Ratio: pass the observed
    per-period Sharpe `sr`, sample length `T`, and `n_trials` = how many strategy/parameter variants were
    tested before reporting this one. Optionally the return `skew`/`kurt`. For agent traders verifying
    backtest data before they trust (or publish) it."""
    return _billed(api_key, "verify_backtest",
                   lambda: claims.verify_claim("backtest", sr=sr, T=T, n_trials=n_trials, skew=skew, kurt=kurt))


@mcp.tool()
def verify_subset_win(api_key: str, p: float, n_tests: int) -> dict:
    """A 'we lead on subset/metric/checkpoint X' claim, corrected for how many you could have picked it from
    (look-elsewhere / multiple comparisons). Pass the raw p-value and the number of comparisons tested."""
    return _billed(api_key, "verify_claim", lambda: claims.verify_claim("subset_win", p=p, n_tests=n_tests))


@mcp.tool()
def verify_model_gap(api_key: str, n: int, p1: float, p2: float) -> dict:
    """Is the accuracy gap between two models real, or below what the test set can resolve? Pass items-per-
    model `n` and the two accuracies. Returns the gap, its significance, and the minimum detectable effect."""
    return _billed(api_key, "verify_claim", lambda: claims.verify_claim("model_gap", n=n, p1=p1, p2=p2))


@mcp.tool()
def verify_judge_bias(api_key: str, wins: int, n: int, p0: float = 0.5) -> dict:
    """Is an LLM-judge / metric preference real, or just longer/first/same-family? Pass the count of verdicts
    the tested side won and the total. Exact binomial vs chance."""
    return _billed(api_key, "verify_claim", lambda: claims.verify_claim("judge_bias", wins=wins, n=n, p0=p0))


@mcp.tool()
def calibrate_judge(api_key: str, judge_caught: list, truth_caught: list) -> dict:
    """Check an LLM judge against ground truth on a labelled slice. Pass aligned booleans: the judge's
    verdicts and the known-correct answers. Returns agreement and whether the judge's errors lean one
    direction (over-crediting = the length/self-preference failure mode)."""
    try:
        _limits.check_list("judge_caught", judge_caught)
        _limits.check_list("truth_caught", truth_caught)
    except ValueError as e:
        return {"error": f"bad request: {e}"}          # reject oversized input before billing
    return _billed(api_key, "calibrate_judge", lambda: _judge.calibrate_judge(judge_caught, truth_caught))


@mcp.tool()
def audit_leaderboard(api_key: str, results: dict, n_boot: int = 1000) -> dict:
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


@mcp.tool()
def issue_receipt(api_key: str, claim_result: dict) -> dict:
    """Turn a verification result into a portable, signed receipt (Ed25519). Attach it to your output; anyone
    can verify — with only the public key — that the claim and verdict were checked and unaltered."""
    priv, pub = _issuer()
    return _billed(api_key, "issue_receipt", lambda: _rcpt.issue_receipt(claim_result, priv, pub))


@mcp.tool()
def balance(api_key: str) -> dict:
    """Your remaining free calls and prepaid credit balance."""
    return credits.balance(api_key)


@mcp.tool()
def pricing() -> dict:
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
