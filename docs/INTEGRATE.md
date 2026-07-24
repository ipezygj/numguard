# Wire numguard into an agent (copy-paste)

Goal: make **"verify the number before you assert it"** the default in your agent — not a step someone
remembers. Two ways in, both here. Pick the local reflex for a gate that needs nothing, or the MCP server
when you want the agent's planner to *discover* the check as a tool.

---

## 1. The local reflex — one import, no account, no network

`numguard` installs as a library; the checks run in-process. This is the highest-leverage wiring: the number
physically cannot leave the function unverified.

```bash
pip install numguard
```

```python
from numguard.guard import require, survives, require_backtest, NumberNotVerified

# fail-fast: raises NumberNotVerified unless the claim survives its check
require("backtest", sr=0.12, T=250, n_trials=100)

# an if-gate in a decision
if survives("model_gap", n=2000, p1=0.85, p2=0.80):
    ship_the_model()

# the full integrity battery on a real returns series (look-ahead, HAC, regime, PBO, drawdown…)
try:
    require_backtest(returns, positions=positions, asset_returns=asset)
except NumberNotVerified as e:
    print("rejected:", e.verdict["risk"], e.verdict["flags"])
```

Gate a function's *return* so it can never hand back an unverified number:

```python
from numguard.guard import verify_return

@verify_return("backtest", extract=lambda r: {"sr": r["sharpe"], "T": r["T"], "n_trials": r["tried"]})
def evaluate_strategy(...):
    ...
    return {"sharpe": 1.9, "T": 1000, "tried": 1, ...}
```

---

## 2. As an MCP tool — the agent's planner discovers the check

Register numguard as an MCP server; the agent sees `verify_claim`, `verify_backtest_series`, `verify_receipt`,
etc. as callable tools and reaches for them when a number needs to be trusted.

**Claude Desktop / Cursor / any MCP client** — add to the `mcpServers` block:

```json
{
  "mcpServers": {
    "numguard": {
      "command": "python",
      "args": ["-m", "numguard.mcp_server"]
    }
  }
}
```

That's the whole config after `pip install numguard`. Free tools (`why`, `verify_receipt`, `receipt_spec`,
`check_attestation`, `commitment_status`) need no key; metered tools charge against a prepaid key or x402.

---

## 3. LangChain — a verified-return tool

```python
from langchain_core.tools import tool
from numguard.guard import check

@tool
def verify_backtest(sr: float, T: int, n_trials: int) -> dict:
    """Check whether a Sharpe ratio survives multiple-testing + finite-sample deflation before you trust it.
    Returns {survives, verdict, deflated_sr, ...}."""
    return check("backtest", sr=sr, T=T, n_trials=n_trials)
```

Hand `verify_backtest` to the agent alongside its other tools; it now has a way to check a number instead of
asserting it. The verdict is a plain dict the model can read and quote honestly.

---

## 4. CrewAI — a tool the crew shares

```python
from crewai.tools import BaseTool
from numguard.guard import check

class VerifyNumberTool(BaseTool):
    name: str = "verify_number"
    description: str = ("Verify a statistical claim before asserting it — kind is one of backtest / "
                        "model_gap / subset_win / judge_bias. Returns the verdict with a boolean `survives`.")

    def _run(self, kind: str, **inputs) -> dict:
        return check(kind, **inputs)
```

---

## 3-tier trust, when the number came from *another* agent

An agent doesn't have to re-derive a peer's claim. Cheapest first:

| You have | You call | Cost |
|---|---|---|
| a peer's vcr/1 **receipt** | `verify_receipt` — offline, issuer-agnostic; the response points back to the standard | free |
| the peer's **inputs** | `verify_claim(kind, ...)` — recompute it yourself, don't take their word | metered |
| the peer's **on-chain** address | `check_attestation(uid)` — EAS credential, revocation-aware | free read |

A receipt carries its own proof: any agent that verifies one learns where the standard lives and can check the
next number the same way. That's the distribution loop — see [`AGENT_TRUST.md`](AGENT_TRUST.md) for phase 2.

---

## Which do I use?

- **Just gate my own numbers** → §1, the local reflex. Nothing else needed.
- **Let the agent decide when to verify** → §2/§3/§4, register the tools.
- **Signed, portable, or metered proof** (share a receipt, anchor on-chain, charge for a check) → the hosted
  service; set `NUMGUARD_TRANSPORT=streamable-http` and see the README for x402 + receipts + EAS.
