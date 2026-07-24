# How to verify a backtest is real (Deflated Sharpe Ratio in Python)

**Short answer:** a good-looking backtest Sharpe is usually inflated by three things — you tried many
strategies and kept the best (multiple testing), the strategy peeked at data it wouldn't have had (look-ahead
leakage), or the sample was too short. To check whether a Sharpe survives, compute the **Deflated Sharpe Ratio**
(Bailey & López de Prado) and run a look-ahead scan. In Python:

```bash
pip install numguard
```
```python
from numguard.guard import require, NumberNotVerified

# a Sharpe of 0.12/period over 250 periods that was the best of 100 configs
try:
    require("backtest", sr=0.12, T=250, n_trials=100)
except NumberNotVerified as e:
    print(e.verdict["verdict"])
    # SR=0.120 over T=250, 100 trials tested; deflation bar=0.160; DSR=0.263
    # -> does NOT survive deflation. (The bar it had to clear, 0.160, is above the Sharpe itself.)
```

The rest of this page explains each check and how to run it.

---

## Why a great backtest Sharpe is usually a lie

Three failure modes turn a real-looking number into fiction:

1. **Multiple testing / selection bias.** If you tried 100 parameter sets and reported the best Sharpe, that
   number is the *maximum of 100 noisy draws*, not the expected performance. The more you tried, the higher the
   bar a Sharpe must clear to be real.
2. **Look-ahead leakage.** If a position uses information from the same bar it trades (or any future bar), the
   equity curve is built from time the strategy never had. It backtests beautifully and fails live.
3. **Finite sample.** A high Sharpe over a few hundred observations has a wide confidence interval that often
   includes zero.

A raw Sharpe ratio ignores all three. The checks below don't.

## The fastest check: the Deflated Sharpe Ratio (DSR)

The **Deflated Sharpe Ratio** discounts a Sharpe for (a) how many strategies you tried, (b) the sample length,
and (c) the return distribution's skew and kurtosis. It answers: *given everything I searched, is this Sharpe
distinguishable from the best I'd expect by luck alone?* A DSR above ~0.95 means the strategy survives.

```python
from numguard.backtest import deflated_sharpe

v = deflated_sharpe(sr=0.15, T=1000, n_trials=1)     # one hypothesis, long sample
print(v)          # DSR=1.000 -> SURVIVES deflation (nothing to deflate: a single, pre-registered test)

v = deflated_sharpe(sr=0.12, T=250, n_trials=100)    # best of 100 on a short sample
print(v.survives, round(v.dsr, 3))                   # False 0.263
```

Inputs: `sr` (Sharpe per period), `T` (number of periods), `n_trials` (how many configurations you tested —
be honest here, this is the whole point), and optionally `skew` / `kurt` of the returns.

Or gate it in one line inside an agent or a research pipeline — `require` raises unless the number survives:

```python
from numguard.guard import require, survives

require("backtest", sr=0.15, T=1000, n_trials=1)     # returns the verdict; raises if it doesn't survive
if survives("backtest", sr=0.12, T=250, n_trials=100):
    ship_it()                                        # this branch never runs — the number is overfit
```

## Beyond one number: the full backtest integrity battery

The Deflated Sharpe still assumes clean, i.i.d.-ish returns. Real backtests fail in ways a single number can't
see. Run the battery on the **actual returns series** to catch them:

```python
from numguard.guard import require_backtest, NumberNotVerified

try:
    require_backtest(returns, positions=positions, asset_returns=asset_returns)
except NumberNotVerified as e:
    print(e.verdict["risk"], e.verdict["flags"])
    # e.g. "critical" ['leakage'] — a look-ahead fraud, or ['drawdown'] / ['hac'] / ['pbo'] ...
```

What it checks:

- **Look-ahead leakage** — compares `SR(positionₜ · assetₜ)` to `SR(positionₜ₋₁ · assetₜ)`; a big gap means the
  strategy is trading on same-bar information.
- **HAC / Newey-West** — autocorrelation inflates a naïve Sharpe; this deflates it to the effective sample size.
- **PBO (Probability of Backtest Overfitting)** — CSCV across a candidate matrix: how often the in-sample best
  is below median out-of-sample.
- **Regime stability** — per-block Sharpe + a structural-break (CUSUM) test.
- **Drawdown / tail** — Calmar, CVaR, realized-vs-expected max drawdown.
- **Block bootstrap, permutation, Ljung-Box, breakeven cost.**

It flags `critical` (leakage, overfitting) down to soft notes, so you know *why* a Sharpe is suspect.

## How to detect look-ahead bias in a backtest

Look-ahead (lookahead) bias is the most common and most fatal backtest error. The tell: a position that
correlates with the *same bar's* return it's supposed to predict.

```python
# a look-ahead fraud: position = sign of the SAME bar's return
returns      = [(1 if a > 0 else -1) * a for a in asset_returns]
positions    = [1.0 if a > 0 else -1.0 for a in asset_returns]

from numguard.guard import require_backtest
require_backtest(returns, positions=positions, asset_returns=asset_returns)
# raises NumberNotVerified: risk="critical", flags include 'leakage'
```

If your backtest's Sharpe collapses when you lag the signal by one bar, it was leaking.

## How to verify someone else's backtest claim

If another person or agent hands you a number, you don't have to re-run their code — if they attached a
**signed receipt**, you can verify offline that the number was actually checked, and by whom:

```python
from numguard.detect import scan          # scan a message / payload for embedded receipts

for hit in scan(their_message):
    if hit["valid"]:
        print("verified:", hit["claim"])  # kind, survives, verdict — signed, unaltered
    else:
        print("do not trust:", hit["reason"])
```

The receipt is an open standard (`vcr/1`), Ed25519-signed, verifiable with only its embedded public key — no
account, no network. A backtest claim without a receipt is just a screenshot.

---

## FAQ

**What is a good Sharpe ratio for a backtest?** There's no universal number — a Sharpe of 2 over 200
observations with 500 trials tried is worse than a Sharpe of 0.8 over 5000 observations tested once. Always
deflate for trials and sample length before comparing.

**What is the Deflated Sharpe Ratio?** A correction (Bailey & López de Prado, 2014) to the Sharpe ratio that
accounts for selection bias from testing multiple strategies, non-normal returns, and sample length. It returns
the probability that the observed Sharpe exceeds what you'd expect from the best of your trials by chance.

**Deflated Sharpe vs Probabilistic Sharpe (PSR)?** PSR is the probability a Sharpe exceeds a benchmark given
one test. The Deflated Sharpe is PSR with the benchmark *raised* to the expected maximum of `n_trials` — i.e.
PSR that accounts for how many strategies you tried.

**How many trials count as "multiple testing"?** Every parameter set, every symbol, every window, every exit
rule you evaluated and discarded. If you grid-searched 10 lookbacks × 10 thresholds, `n_trials=100`.

**Is this only for trading backtests?** The same statistics catch overfit eval claims too — a cherry-picked
subset win, a model-accuracy gap below the detectable effect, a biased LLM judge. See the
[proof gallery](PROOF_GALLERY.md) for worked examples of each, and [INTEGRATE.md](INTEGRATE.md) to wire the
checks into an agent.

---

*`numguard` is an open-source verification layer for the numbers agents assert — Deflated Sharpe, a backtest
integrity battery, and signed receipts anyone can verify offline. [Repo](https://github.com/ipezygj/numguard) ·
`pip install numguard`.*
