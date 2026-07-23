# Deploy — one service, settles to your own wallet, no third party

numguard settles x402 payments **itself**: it verifies the agent's signed USDC authorization and submits it
on-chain, paying gas from a dedicated wallet. USDC lands straight in your wallet. No Coinbase, no separate
facilitator service, no internal-networking to break.

## Two wallets — keep them separate

| Wallet | Env var | Holds | Key on server? |
|---|---|---|---|
| **Receiving** | `NUMGUARD_PAYTO` | the USDC you earn | **No** — public address only |
| **Gas** | `NUMGUARD_GAS_KEY` | a little **ETH on Base** (settlement gas) | Yes — so make it a **dedicated** hot wallet |

The receiving wallet (`0x75c5…836f`) never needs a key on the server. numguard needs a signer key only to
*broadcast* each settlement (the payer's own signature authorises the USDC transfer; your gas wallet just pays
the gas). If the gas key is ever compromised, the loss is capped at its small ETH balance — your earnings sit
safely in the separate receiving wallet.

## Deploy (Render)

`render.yaml` is a single web service. Push, then in the Render dashboard set:

- `NUMGUARD_PAYTO` — your receiving wallet (already baked in)
- `NUMGUARD_NETWORK` — `base` (mainnet) or `base-sepolia` (testnet)
- `NUMGUARD_GAS_KEY` — the dedicated gas wallet's private key *(secret; set in the dashboard, never commit)*
- `NUMGUARD_RPC` — *(optional)* a private Base RPC (Alchemy/Ankr) if the public one rate-limits

Health: `curl https://<your-service>.onrender.com/health` · Pricing: `.../pricing`.

Locally: `pip install -e ".[settle,receipts]"` then `uvicorn numguard.rest_api:app`. With `NUMGUARD_PAYTO`
unset the endpoints run **free** (dev mode); with it set and no `NUMGUARD_GAS_KEY`, they **refuse** (never
serve unpaid).

## How a payment flows

1. Agent POSTs to a tool → gets **HTTP 402** with the price, your `payTo`, the USDC address, and the token's
   EIP-712 domain (`scheme:"exact"`, `network:"base"`, x402 v1).
2. Agent signs a USDC **EIP-3009 `transferWithAuthorization`** (gasless for it) and retries with `X-PAYMENT`.
3. numguard **verifies** the signature recovers to the payer, checks `payTo`/amount/validity, and confirms the
   payer's on-chain USDC balance.
4. numguard **submits** the authorization to the USDC contract (gas from `NUMGUARD_GAS_KEY`) → USDC moves from
   the payer to your `payTo` → the tool result is returned with the settlement tx.

You never make a payment yourself — paying agents bring their own wallets; you only receive.

## Verifying the pipe without spending money

`python scripts/handshake_test.py https://<your-service>.onrender.com/verify_backtest` generates a throwaway
(unfunded) key, signs a correct payment, and posts it. Expected result: **`insufficient funds`** — which proves
the whole verify→settle path parsed and ran; only a funded wallet is missing. A funded agent would settle.

## Test on Base Sepolia first (free), then flip to mainnet

Set `NUMGUARD_NETWORK=base-sepolia`, fund the gas wallet with faucet Base-Sepolia ETH, and pay with test-USDC
for $0. Confirm a funded payer gets a settlement tx. Then set `NUMGUARD_NETWORK=base` for real USDC.

## Hardening (a live, real-money endpoint)

Built into numguard (`numguard/rest_api.py`), applied on **both** the REST and MCP rails:
- **Rate limiting** — 60 req/60s per client IP (429 over that).
- **Input caps** — list/matrix payloads capped at 5,000 items, `n_boot` at 5,000, body at 256 KB, so a huge
  payload can't pin the CPU. (Shared caps in `numguard/_limits.py`; the MCP tools enforce them too.)
- **Clean errors** — bad input returns a 400 (never a 500 with a traceback).
- **Settlement safety** — a payment whose signature doesn't recover to the payer is never settled; with no
  `NUMGUARD_GAS_KEY` the service refuses rather than serving unpaid.

**Keys:** `NUMGUARD_GAS_KEY` is a dedicated gas-only hot wallet, never your receiving wallet, only ever in the
Render env. Keep its ETH balance small and watch it on Basescan; rotate + redeploy if it drains oddly.

## Networks & addresses (baked in)

- Base mainnet: network `base` / CAIP-2 `eip155:8453`, USDC `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`
- Base Sepolia: network `base-sepolia` / CAIP-2 `eip155:84532`, USDC `0x036CbD53842c5426634e7929541eC2318f3dCF7e`
