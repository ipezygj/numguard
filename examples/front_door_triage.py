"""The front door: describe what you're about to do or assert, get routed to the right check.

    python examples/front_door_triage.py

No LLM, no network — deterministic keyword routing across the whole trust layer (numguard verification,
agent-guard safety, evalgate stats). When an agent doesn't know which tool to reach for, it calls triage first.
"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # em-dashes in the routes vs a cp1252 console
except Exception:
    pass
from numguard import triage as T

INTENTS = [
    "I'm about to publish a backtest with a Sharpe of 20",
    "should I run this npm install of a package I just found",
    "about to claim our model beats the baseline on accuracy",
    "a peer sent me a number with a receipt — can I trust it",
    "we're going live and I want to prove the track record isn't cherry-picked after the fact",
    "does this text leak an API key before I commit it",
    "make this verified result a permanent on-chain credential",
    "is our leaderboard #1 real or a coin flip",
]


def main():
    for intent in INTENTS:
        r = T.triage(intent)
        if not r["matched"]:
            print(f"- {intent}\n    -> {r['reflex']}\n")
            continue
        rt = r["route"]
        print(f"- {intent}")
        print(f"    -> [{rt['product']}] {rt['tool']}  ({rt['kind']})")
        print(f"      {r['reflex']}")
        print(f"      call: {rt['call']}\n")


if __name__ == "__main__":
    main()
