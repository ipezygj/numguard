# Proof gallery — numguard on real numbers

> Dogfooding, not decoration. Each row is a number someone might assert, run through numguard's **real**
> check, with a **real** signed receipt. Nothing here is fabricated usage — the verdicts are what the
> checker actually returns. Of 8 cases it lets **3 through** and **flags 5**.
> That discrimination is the product: it accepts real signal and rejects the fake.

Issuer public key (verify any receipt offline against this): `ade9af6896d37ace4b7cb8dc10e31c99a937a7b69773da757db9893f9503180b`

| # | The asserted number | Check | Verdict |
|---|---|---|---|
| 1 | A Sharpe of 0.12/period that was the best of 100 configs | `backtest` | 🚫 flagged |
| 2 | A single hypothesis, long sample, Sharpe 0.15 | `backtest` | ✅ survives |
| 3 | Model A beats B by 5 points over 2000 items each | `model_gap` | ✅ survives |
| 4 | Model A beats B by 1 point over 200 items each | `model_gap` | 🚫 flagged |
| 5 | "We win on subset X, p=0.03" — best of 20 subsets | `subset_win` | 🚫 flagged |
| 6 | An LLM judge prefers our answer 68/100 | `judge_bias` | 🚫 flagged |
| 7 | A judge splits 54/100 | `judge_bias` | ✅ survives |
| 8 | A backtest positioned on the same bar it trades | `backtest_series` | 🚫 flagged |

---

### 1. A Sharpe of 0.12/period that was the best of 100 configs  — 🚫 **flagged**

*The claim:* "Our strategy Sharpes well — 0.12 per period over 250 periods."

*Why it's here:* Canonical selection bias: at one hypothesis it looks fine, but it was the best of 100 tried. The Deflated Sharpe (Bailey & Lopez de Prado, 2014) discounts the search and it collapses.

*numguard's verdict:* SR=0.120 over T=250, 100 trials tested; deflation bar=0.160; DSR=0.263 -> does NOT survive deflation (needs DSR>0.95). PSR-vs-0=0.970, minTRL=190 obs.

*Receipt digest:* `f969c864b5065ea85af26bd5015cd56c6b24e4ab59f4a23284d8997521f50526`  
*Verify it yourself:* `receipt_spec.verify_any(<this receipt>)` → `valid: true` (offline, issuer-agnostic — you need nothing but the receipt).

### 2. A single hypothesis, long sample, Sharpe 0.15  — ✅ **survives**

*The claim:* "One idea, pre-registered, tested once on 1000 periods: Sharpe 0.15."

*Why it's here:* Nothing to deflate — one trial, a long track record. A genuine edge should, and does, survive.

*numguard's verdict:* SR=0.150 over T=1000, 1 trials tested; deflation bar=0.000; DSR=1.000 -> SURVIVES deflation (needs DSR>0.95). PSR-vs-0=1.000, minTRL=123 obs.

*Receipt digest:* `19ff2286afb78823f9a85ef1b49a9a44c0813280bec3c00e1dde3ee0526e9ac1`  
*Verify it yourself:* `receipt_spec.verify_any(<this receipt>)` → `valid: true` (offline, issuer-agnostic — you need nothing but the receipt).

### 3. Model A beats B by 5 points over 2000 items each  — ✅ **survives**

*The claim:* "A is better: 85% vs 80% on 2000 held-out items."

*Why it's here:* A 5-point gap at n=2000 sits above the minimum detectable effect — a real ranking, not noise.

*numguard's verdict:* gap=+0.05 over n=2000; MDE at 80% power=0.0337; real difference.

*Receipt digest:* `fd97c7385b4b398c2f32b5715360b2d9d5ce52017ce820555e69a4ebdbb5463c`  
*Verify it yourself:* `receipt_spec.verify_any(<this receipt>)` → `valid: true` (offline, issuer-agnostic — you need nothing but the receipt).

### 4. Model A beats B by 1 point over 200 items each  — 🚫 **flagged**

*The claim:* "A wins: 85% vs 84% — ship A."

*Why it's here:* A 1-point gap at n=200 is below the MDE: indistinguishable at that sample. Not a ranking.

*numguard's verdict:* gap=+0.01 over n=200; MDE at 80% power=0.101; below MDE — indistinguishable at n=200, not a ranking.

*Receipt digest:* `2d38664074fc7ede171a0ec9fa5df0d910423a4a43e06d258837a1fcdef70ecd`  
*Verify it yourself:* `receipt_spec.verify_any(<this receipt>)` → `valid: true` (offline, issuer-agnostic — you need nothing but the receipt).

### 5. "We win on subset X, p=0.03" — best of 20 subsets  — 🚫 **flagged**

*The claim:* "Significant advantage on subset X (p=0.03)."

*Why it's here:* The p looks significant until you count the family: it was the best of 20 subsets. After multiple-testing correction it's within noise.

*numguard's verdict:* Best-of-20: raw p=0.03 -> sidak p=0.456; does NOT survive — within noise for the family tested.

*Receipt digest:* `e7ca53d6a14aa2fa66bcdc9e64c4fe597d9cb98bde849675c042a824644ec54f`  
*Verify it yourself:* `receipt_spec.verify_any(<this receipt>)` → `valid: true` (offline, issuer-agnostic — you need nothing but the receipt).

### 6. An LLM judge prefers our answer 68/100  — 🚫 **flagged**

*The claim:* "The judge picks ours 68% of the time — clearly better."

*Why it's here:* 68/100 is significantly above chance — but a judge preference this size often tracks length, position, or same-family style, not quality. Flagged as bias to investigate.

*numguard's verdict:* 68/100=68.0% (p=0.000409); significant bias — the winner may just be longer/first/same-family.

*Receipt digest:* `4149a21be375ab7e53ec030a6d171b479ed3cb53e6f755389e4331933c17c721`  
*Verify it yourself:* `receipt_spec.verify_any(<this receipt>)` → `valid: true` (offline, issuer-agnostic — you need nothing but the receipt).

### 7. A judge splits 54/100  — ✅ **survives**

*The claim:* "Ours wins 54% of head-to-heads."

*Why it's here:* 54/100 is indistinguishable from a coin flip — no evidence of bias, and no evidence of an edge.

*numguard's verdict:* 54/100=54.0% (p=0.484); no significant bias vs chance.

*Receipt digest:* `7bc3ee92e3ffabb743a8b31b71e1d2626930c3f3680031cb7acc7cba7788467b`  
*Verify it yourself:* `receipt_spec.verify_any(<this receipt>)` → `valid: true` (offline, issuer-agnostic — you need nothing but the receipt).

### 8. A backtest positioned on the same bar it trades  — 🚫 **flagged**

*The claim:* "The full series backtests beautifully."

*Why it's here:* The integrity battery reads the actual returns: positions are taken on the same bar's move — look-ahead leakage. A Sharpe built from time the strategy never had. Flagged CRITICAL.

*numguard's verdict:* CRITICAL — flagged by 2 check(s): drawdown, leakage. Treat the headline Sharpe with suspicion.
  
*Risk:* `critical` — flags: `['drawdown', 'leakage']`

*Receipt digest:* `226c3ef9a01a551488eca1fb621692ccce25ce0608dc57c7e89b57bee1664aef`  
*Verify it yourself:* `receipt_spec.verify_any(<this receipt>)` → `valid: true` (offline, issuer-agnostic — you need nothing but the receipt).

---

## Verify the whole gallery yourself

```bash
pip install numguard
python -c "import json; from numguard import receipt_spec as S; \
  b=json.load(open('docs/proof_gallery.json')); \
  print(all(S.verify_any(c['receipt'])['valid'] for c in b['cases']), 'all receipts verify')"
```

Every receipt is signed by the key above and checks out against it — no numguard account, no network.
That's the whole idea: don't believe the gallery, *verify* it.
