"""langgraph_guardrail — numguard as a guardrail node in a LangGraph agent.

Drop a verification node into the graph between "the model produced a number" and "the agent acts on / posts
it". The node checks the claim; on a fail it routes to a correction branch instead of letting the number
through. This is the framework-native pattern Kimi flagged as the #1 reach move — be the default guardrail
inside the graph, not a wrapper in a repo nobody visits.

    pip install numguard langgraph
    python examples/langgraph_guardrail.py

If langgraph isn't installed, the core guardrail function still runs so you can see the routing logic.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from numguard.guard import check


def numguard_guardrail(state: dict) -> dict:
    """A LangGraph node: read the pending numeric claim from state, verify it, and annotate the verdict.
    State in:  {"claim": {"kind": "...", "inputs": {...}}, ...}
    State out: adds {"verified": bool, "verdict": "...", "risk": ...}. Downstream edges route on `verified`."""
    claim = state.get("claim") or {}
    verdict = check(claim.get("kind", "backtest"), **claim.get("inputs", {}))
    return {**state,
            "verified": bool(verdict.get("survives")),
            "verdict": verdict.get("verdict"),
            "risk": verdict.get("risk")}


def route_after_guardrail(state: dict) -> str:
    """Conditional edge: send verified numbers forward, unverified ones to a correction node."""
    return "act" if state.get("verified") else "revise"


def _build_graph():
    """Assemble a minimal StateGraph: guardrail -> (act | revise). Requires langgraph."""
    from langgraph.graph import StateGraph, END
    g = StateGraph(dict)
    g.add_node("guardrail", numguard_guardrail)
    g.add_node("act", lambda s: {**s, "output": f"POSTED (verified): {s['verdict']}"})
    g.add_node("revise", lambda s: {**s, "output": f"HELD for revision (unverified): {s['verdict']}"})
    g.set_entry_point("guardrail")
    g.add_conditional_edges("guardrail", route_after_guardrail, {"act": "act", "revise": "revise"})
    g.add_edge("act", END)
    g.add_edge("revise", END)
    return g.compile()


def _demo(claim):
    out = numguard_guardrail({"claim": claim})
    branch = route_after_guardrail(out)
    print(f"  claim={claim['inputs']} -> verified={out['verified']} -> route: {branch}")
    print(f"    {out['verdict']}")


if __name__ == "__main__":
    real = {"kind": "backtest", "inputs": {"sr": 0.15, "T": 1000, "n_trials": 1}}
    overfit = {"kind": "backtest", "inputs": {"sr": 0.12, "T": 250, "n_trials": 100}}
    try:
        app = _build_graph()
        print("LangGraph compiled. Running the graph:\n")
        for c in (real, overfit):
            print(" ", app.invoke({"claim": c})["output"])
    except ImportError:
        print("(langgraph not installed — showing the guardrail node + routing directly)\n")
        _demo(real)
        _demo(overfit)
    print("\nThe overfit number never reaches 'act'. The guardrail routes it to revision instead.")
