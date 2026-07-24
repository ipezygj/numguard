# Agent-to-agent trust — a design sketch (phase 2)

> Status: **design only.** This is the direction numguard grows once agents actually transact with each other.
> The pieces below mostly exist; what's missing is a discovery/challenge flow and, above all, the demand side.
> Do **not** build the marketplace before the agents show up (same lesson as the coin and the bond).

## The problem

Everything numguard ships today serves **one** agent checking **its own** number. The next layer is the harder
one: agent **A** wants to trust, buy, or allocate to agent **B**'s output — a backtest, a model score, a claim —
and A cannot take B's word for it. Today A either trusts blindly or re-derives everything itself.

## The move: trust the check, not the agent

numguard sits between them as a **neutral referee**. Trust never flows from A to B; it flows to a *check* that
needs trust in no one:

```
B: verify_claim(...) → signed vcr/1 receipt   (B proves its number was checked, and by which key)
B: publishes the number WITH the receipt        (a number without a receipt looks unchecked)
A: verify_receipt(B's receipt)  → free, offline, issuer-agnostic
   → "checked by numguard's key, verdict = survives, kind = backtest" — A trusts that, not B
```

Three tiers, cheapest first:

| Tier | A does | Trust it gives | Cost |
|---|---|---|---|
| **Receipt** | `verify_receipt` on B's vcr/1 receipt | the number was checked, by whom, with what verdict | free |
| **Re-verify** | asks numguard to recompute B's claim from B's inputs | independent recomputation, not just B's word | metered |
| **On-chain** | `check_attestation` on B's EAS credential (by B's address) | composable, queryable by *any* protocol, revocation-aware | free read |

The strength ladder matters: a **proof-of-check** receipt (verify_backtest / battery / model-gap) needs trust in
no one — numguard computed it. A **track-record** receipt is `data_source: self_reported` — honest signal, not
proof of real performance. A must read the trust level, and numguard already labels it.

## What's missing (the actual work, when the time comes)

1. **A challenge/request flow.** A protocol verb: A → B "prove claim X", B → A a receipt/attestation for it.
   Today B has to volunteer the receipt; there's no standard ask.
2. **Discovery.** Where does A *find* B and its verified record? An index of agents-with-verified-claims (the
   EAS attestations already form the on-chain half). Keep it a **queryable record, not a score** — the moment
   it becomes a single number to rank on, it gets Goodharted (the exact failure numguard exists to catch).
3. **A verifiable feed for live claims.** Track records are self-reported and stay weak until an agent's
   strategy runs where numguard can read its P&L independently — an on-chain vault. That's the honest segment
   to start with (Kimi's "score already-on-chain strategies"), not self-reported returns.

## Why phase 2, and the one guardrail

This is worthless until agents transact and an allocator side exists — building the trust market into an empty
room is the same mistake as a token with no pool. The pieces (receipts, free verify, EAS, `check_attestation`,
the `guard` reflex) are the **foundation**; the broker is what you assemble on top **once one real integration
proves the demand**. Build that integration first; let it tell you which of the three missing pieces matters.
