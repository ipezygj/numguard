# Self-sovereign deploy — your own facilitator, no Coinbase, no third party

This runs numguard **and** its payment facilitator ([`x402-rs`](https://github.com/x402-rs/x402-rs)) as your
own services. Nothing external holds your funds or data. Agents pay USDC that settles straight to your wallet.

## Two wallets — keep them separate

| Wallet | Env var | Holds | Key on server? |
|---|---|---|---|
| **Receiving** | `NUMGUARD_PAYTO` | the USDC you earn | **No** — public address only |
| **Facilitator (gas)** | `FACILITATOR_PRIVATE_KEY` | a little **ETH on Base** (to pay settlement gas) | Yes — so make it a **dedicated** hot wallet |

The receiving wallet (yours, `0x75c5…836f`) never needs a key on the server. The facilitator needs a signer
key to broadcast the on-chain USDC transfers, so it pays gas — fund a **separate** wallet with a small amount
of ETH on Base and use *that* key. If it's ever compromised, the loss is capped at its gas balance; your
earnings sit safely in the receiving wallet.

## Deploy

```bash
cp .env.example .env
# edit .env: set FACILITATOR_PRIVATE_KEY (dedicated gas wallet), keep NUMGUARD_NETWORK=base-sepolia to test
docker compose up -d
```

- `numguard` is on `:8080` (the public paid API + MCP). The facilitator stays internal (not exposed).
- Health: `curl localhost:8080/health` · Pricing: `curl localhost:8080/pricing`.
- On a host (Render/Fly/a VPS): deploy the same compose; expose only the `numguard` service.

## Test with free test-USDC first, then flip to real money

1. **Testnet** (`NUMGUARD_NETWORK=base-sepolia`): fund the gas wallet with Base-Sepolia ETH (a faucet), grab
   test-USDC, and run the full pay-per-call loop for $0.00 real. Confirm an agent gets a `402`, pays, and the
   result comes back with a settlement tx.
2. **Mainnet**: set `NUMGUARD_NETWORK=base`, fund the gas wallet with a little real ETH on Base, redeploy.
   USDC now settles to `NUMGUARD_PAYTO`.

## One thing to verify on the first testnet run (honest note)

numguard emits an `exact`-scheme x402 challenge (CAIP-2 network + the on-chain USDC address). x402-rs and the
paying agent's x402 client must agree on the **scheme/version** and the `network` string. If `/verify` rejects
a well-formed payment, it's almost always this handshake — align the `scheme` in `facilitator/config.json`
(and the `network` casing) with what your agent's x402 client sends. This is the only integration point that
wants a real testnet round-trip to lock down; everything else is wired. (Built ready to test; not yet
round-tripped against a live agent.)

## Networks & addresses (baked in)

- Base mainnet `eip155:8453`, USDC `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`
- Base Sepolia `eip155:84532`, USDC `0x036CbD53842c5426634e7929541eC2318f3dCF7e`
- Public RPCs in `facilitator/config.json` (`mainnet.base.org` / `sepolia.base.org`) — swap for a private RPC (Alchemy/Ankr) if you hit rate limits.
