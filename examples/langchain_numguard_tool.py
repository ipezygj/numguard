"""langchain_numguard_tool — give an agent a way to CHECK a number instead of asserting it.

Wrap numguard's local checks as LangChain tools. The agent's planner sees them and reaches for one when a
number needs to be trusted — a backtest Sharpe, a model-accuracy gap, an LLM-judge preference. The check runs
in-process (no account, no network); the verdict is a plain dict the model can read and quote honestly.

    pip install numguard langchain-core
    python examples/langchain_numguard_tool.py

If langchain isn't installed, this still runs a plain-function demo so you can see the verdicts.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from numguard.guard import check


def verify_backtest(sr: float, T: int, n_trials: int = 1) -> dict:
    """Check whether a Sharpe ratio survives multiple-testing + finite-sample deflation before you trust it.
    sr: Sharpe per period, T: number of periods, n_trials: how many configs you tried. Returns a verdict
    with a boolean `survives` and the deflated numbers."""
    return check("backtest", sr=sr, T=T, n_trials=n_trials)


def verify_model_gap(n: int, p1: float, p2: float) -> dict:
    """Check whether an accuracy gap between two models is bigger than the test set can resolve. n: items per
    model, p1/p2: the two accuracies. Returns a verdict with `survives` (real ranking) or not (below MDE)."""
    return check("model_gap", n=n, p1=p1, p2=p2)


def verify_judge_bias(wins: int, n: int) -> dict:
    """Check whether an LLM judge's preference is real or just noise/position/length bias. wins: times it
    picked your answer out of n head-to-heads. Returns a verdict with `survives` (no bias) or flagged."""
    return check("judge_bias", wins=wins, n=n)


def as_langchain_tools():
    """Return the three checks as LangChain StructuredTools, ready to hand to an agent. Raises ImportError if
    langchain-core isn't installed — that's the only dependency this adds."""
    from langchain_core.tools import StructuredTool
    return [StructuredTool.from_function(f)
            for f in (verify_backtest, verify_model_gap, verify_judge_bias)]


if __name__ == "__main__":
    try:
        tools = as_langchain_tools()
        print(f"registered {len(tools)} numguard tools for an agent: {[t.name for t in tools]}\n")
    except ImportError:
        print("(langchain-core not installed — showing the plain-function verdicts)\n")

    print("verify_backtest(sr=0.12, T=250, n_trials=100):")
    print("  ", verify_backtest(0.12, 250, 100)["verdict"])
    print("verify_model_gap(n=2000, p1=0.85, p2=0.80):")
    print("  ", verify_model_gap(2000, 0.85, 0.80)["verdict"])
    print("verify_judge_bias(wins=68, n=100):")
    print("  ", verify_judge_bias(68, 100)["verdict"])
    print("\nAn agent holding these tools can check a number before it asserts it — the reflex, not a step.")
