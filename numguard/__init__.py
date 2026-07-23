"""numguard — the verification layer for the agent economy.

Agents generate an explosion of numbers (eval scores, A/B results, benchmark claims, backtest Sharpes). The
scarce resource is trust in those numbers. numguard is the callable primitive an agent uses to check a number
before it asserts it — and to attach a portable, signed receipt proving it was checked.

Built on `evalgate` for shared eval statistics; adds the Deflated Sharpe Ratio for backtests (agent traders),
signed reproducibility receipts, prepaid + x402 metering, and an MCP server so any agent can call it.
"""
from .backtest import deflated_sharpe, probabilistic_sharpe_ratio, expected_max_sharpe, cost_haircut, sharpe
from .claims import verify_claim, KINDS
from .judge import calibrate_judge
from .receipt import issue_receipt, verify_receipt, keypair
from . import credits, x402

__version__ = "0.1.0"
__all__ = [
    "verify_claim", "KINDS",
    "deflated_sharpe", "probabilistic_sharpe_ratio", "expected_max_sharpe", "cost_haircut", "sharpe",
    "calibrate_judge",
    "issue_receipt", "verify_receipt", "keypair",
    "credits", "x402",
]
