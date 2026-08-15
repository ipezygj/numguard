"""numguard — the verification layer for the agent economy.

Agents generate an explosion of numbers (eval scores, A/B results, benchmark claims, backtest Sharpes). The
scarce resource is trust in those numbers. numguard is the callable primitive an agent uses to check a number
before it asserts it — and to attach a portable, signed receipt proving it was checked.

Built on `evalgate` for shared eval statistics; adds the Deflated Sharpe Ratio for backtests (agent traders),
signed reproducibility receipts, prepaid + x402 metering, and an MCP server so any agent can call it.
"""
from .backtest import deflated_sharpe, probabilistic_sharpe_ratio, expected_max_sharpe, cost_haircut, sharpe
from .fdr import fdr_hurdle, harvey_liu_hurdle, hurdle_curve
from .claims import verify_claim, KINDS
from .judge import calibrate_judge
from .receipt import issue_receipt, verify_receipt, keypair
from . import credits, x402

# Pinned to pyproject by test_package_api.py — a package that misreports its own
# version is the smallest possible version of the thing this library exists to catch.
__version__ = "0.2.4"
__all__ = [
    "verify_claim", "KINDS",
    "deflated_sharpe", "probabilistic_sharpe_ratio", "expected_max_sharpe", "cost_haircut", "sharpe",
    "fdr_hurdle", "harvey_liu_hurdle", "hurdle_curve",
    "calibrate_judge",
    "issue_receipt", "verify_receipt", "keypair",
    "credits", "x402",
]
