# numguard

**The verification layer for the agent economy — an agent-callable primitive that checks a number before it gets asserted, and hands back a signed receipt proving it was checked.**

Agents now produce an explosion of numbers: eval scores, A/B results, "the agent improved 12%", benchmark rankings, backtest Sharpes. The scarce resource isn't the number — it's *trust* in the number. `numguard` is the tool an agent calls mid-task to ask **"does this survive a second look?"**, and to attach a portable, tamper-evident receipt so the answer travels with the claim.

Built on [`evalgate`](https://github.com/ipezygj/evalgate) for the shared eval statistics; adds the pieces agents specifically need — a **Deflated Sharpe Ratio** for backtests, **judge calibration**, **signed receipts**, and **metering an agent can actually pay** (prepaid credits + x402 pay-per-call). Exposed as an **MCP server**, so any agent can call it.

---

## The tools

| MCP tool | What an agent asks it |
|---|---|
| **`verify_backtest`** | *Is this strategy's Sharpe real, or the luckiest of the many I tried?* (Deflated Sharpe Ratio) |
| `verify_subset_win` | *Does "we lead on subset X" survive correcting for how many subsets I tested?* |
| `verify_model_gap` | *Is the gap between these two models bigger than the test set can resolve?* |
| `verify_judge_bias` | *Is my judge's preference real, or just longer / first / same-family?* |
| `calibrate_judge` | *Is the LLM judge I trust actually calibrated against ground truth?* |
| `audit_leaderboard` | *Is #1 on this leaderboard statistically real?* (rank confidence intervals) |
| `issue_receipt` | *Give me a signed token proving this number was checked.* |
| `pricing` / `balance` | machine-readable price list + credit balance |

## For agent traders: the Deflated Sharpe Ratio

The number that kills a backtest is the same one that kills a benchmark score: **you tried many, and you reported the best.** In finance the rigorous correction is the Deflated Sharpe Ratio (Bailey & López de Prado) — given how many variants you tested, what Sharpe would the *luckiest zero-skill* strategy have shown, and do you beat it after adjusting for sample length and non-normal returns?

```python
from numguard import deflated_sharpe

deflated_sharpe(sr=0.12, T=250, n_trials=100)
# SR=0.120 over T=250, 100 trials tested; deflation bar=0.160; DSR=0.263
# -> does NOT survive deflation.  (PSR-vs-0=0.970 — it LOOKS significant on a single test.)

deflated_sharpe(sr=0.15, T=1000, n_trials=1)
# DSR=1.000 -> SURVIVES. A real edge over a long sample.
```

The contrast is the whole point: a single-test probability of **0.97** ("significant!") collapses to a deflated **0.26** ("noise") once you account for the 100 strategies that were tried. An agent optimizing over strategies should call this before it trusts — or publishes — a backtest.

## Signed receipts (the part that compounds)

```python
from numguard import verify_claim, issue_receipt, verify_receipt, keypair
priv, pub = keypair()
result  = verify_claim("backtest", sr=0.12, T=250, n_trials=100)
receipt = issue_receipt(result, priv, pub)     # Ed25519-signed
verify_receipt(receipt)                          # True — anyone can verify with the public key alone
```

Attach the receipt to your output. A downstream agent (or human) can confirm — without your keys — that the claim and its verdict weren't altered and that numguard issued them. As receipts circulate, "a number without a receipt" starts to read like "a number nobody checked."

## Buying is easy for an agent

Two rails, both built so an agent can decide and pay **in-loop**, no human clicking:

1. **Prepaid credits + API key** — a human tops up once; the agent spends per call. Generous free tier (25 calls/key) so the agent feels the value first, then a machine-readable price list. Insufficient balance returns a structured `payment_required`, not an error.
2. **x402 pay-per-call** — the agent hits a tool, gets an HTTP-402 with a machine-readable price + pay-to address, pays USDC from its wallet, retries with proof, gets the result. The protocol layer is here; settlement is pluggable (inject a facilitator/RPC verifier for production).

```python
from numguard import x402
x402.require_payment("verify_backtest", price_usd=0.03, pay_to="0x…")
# -> {"status": 402, "accepts": [{"scheme":"exact","network":"base","asset":"USDC", ...}]}
```

## Run the MCP server

```bash
pip install git+https://github.com/ipezygj/numguard
python -m numguard.mcp_server        # stdio MCP server; point your agent/host at it
```

Then an agent calls e.g. `verify_backtest(api_key="…", sr=0.12, T=250, n_trials=100)` and gets a verdict it can quote and a receipt it can attach.

## Deploy it (hosted, paid, discoverable)

**1. Host the paid HTTP API** (x402 per-call):

```bash
docker build -t numguard . && docker run -p 8080:8080 \
  -e NUMGUARD_PAYTO=0xYOURWALLET \
  -e NUMGUARD_FACILITATOR_URL=https://your-x402-facilitator \
  numguard
```

Or one-click on Render: **New → Blueprint → this repo** (`render.yaml` included); set `NUMGUARD_PAYTO` +
`NUMGUARD_FACILITATOR_URL` in the dashboard. With `NUMGUARD_PAYTO` **unset** the API runs **free (dev mode)**
so you can test before wiring a wallet. Endpoints: `POST /verify_backtest`, `/verify_model_gap`, … ; `GET /pricing`.

The x402 flow, end to end: the agent POSTs → gets `402` with an `accepts` block (price, `payTo`, network) →
signs a USDC payment → retries with an `X-PAYMENT` header → numguard verifies + settles it through the
facilitator to your wallet → returns the result. Settlement is the real x402 `/verify` + `/settle` handshake
(`numguard.x402.facilitator_verifier`) — **facilitator-agnostic**: point `NUMGUARD_FACILITATOR_URL` at any
x402 facilitator. Options:

- **Testnet (free, no account):** `https://x402.org/facilitator` with `NUMGUARD_NETWORK=base-sepolia` — test the whole flow with test-USDC first.
- **Mainnet, self-sovereign:** self-host [`x402-rs`](https://github.com/x402-rs) (open-source, no third party) and point at your own URL.
- **Mainnet, hosted (non-Coinbase):** thirdweb or PayAI facilitators (Base) — set `NUMGUARD_FACILITATOR_AUTH` if the facilitator needs a key.

**2. Serve the MCP server over HTTP** (for remote MCP hosts): `uvicorn numguard.mcp_server:app` (or
`NUMGUARD_TRANSPORT=streamable-http python -m numguard.mcp_server`).

**3. Get discovered:** `server.json` (official MCP registry) and `smithery.yaml` (Smithery) ship in the repo;
connect the repo at those registries so agents can find the server. GitHub topics: `mcp`, `mcp-server`, `x402`.

## Design notes

- Statistics are shared with `evalgate` (zero-dependency); numguard adds the backtest, receipt, metering, and MCP layers on top — it does not re-implement the core checks.
- Pure-`math` numerics where possible; `cryptography` only for Ed25519 receipts (HMAC fallback without it).
- Every verdict is derived from a computed statistic, never asserted — the same discipline as the book behind it, *Measured, Not Believed* (leanpub.com/measurednotbelieved).

MIT.
