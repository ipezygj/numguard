"""Shared input-size limits.

A public tool called by untrusted agents must guard its own input on EVERY rail — the REST endpoint
*and* the MCP tool reach the same compute, so the caps live here and both import them. (Without this, an
agent could bypass the REST caps by calling the identical tool over MCP with an oversized payload.)
"""
from __future__ import annotations

MAX_ITEMS = 5000          # cap list/matrix sizes so a huge payload can't pin the CPU
MAX_NBOOT = 5000          # cap leaderboard bootstrap iterations


def check_list(name: str, v) -> None:
    """Raise ValueError unless v is a list/tuple within MAX_ITEMS."""
    if not isinstance(v, (list, tuple)) or len(v) > MAX_ITEMS:
        raise ValueError(f"{name} must be a list of <= {MAX_ITEMS} items")


def check_leaderboard(results, n_boot) -> None:
    """Raise ValueError unless the leaderboard payload is within the item/bootstrap caps."""
    if not isinstance(results, dict):
        raise ValueError("results must be an object")
    total = sum(len(v) for v in results.values() if hasattr(v, "__len__"))
    if total > MAX_ITEMS:
        raise ValueError(f"results too large (> {MAX_ITEMS} total items)")
    if int(n_boot) > MAX_NBOOT:
        raise ValueError(f"n_boot must be <= {MAX_NBOOT}")
