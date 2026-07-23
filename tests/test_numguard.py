"""Tests — also executable documentation of what each tool claims."""
import tempfile
from pathlib import Path

from numguard import (
    verify_claim, deflated_sharpe, cost_haircut, calibrate_judge,
    issue_receipt, verify_receipt, keypair, credits,
)
from numguard import x402


# ---- backtest: the star module (agent traders) ----
def test_deflated_sharpe_refutes_overfit_and_certifies_real():
    weak = deflated_sharpe(sr=0.12, T=250, n_trials=100)   # best of 100 tries
    assert not weak.survives and weak.dsr < 0.95
    strong = deflated_sharpe(sr=0.15, T=1000, n_trials=1)
    assert strong.survives and strong.dsr > 0.95
    # single-test PSR can look significant while DSR does not — the whole point
    assert weak.psr > 0.9 and weak.dsr < weak.psr


def test_cost_haircut_reduces_sharpe():
    rets = [0.0004 if i % 2 == 0 else -0.0001 for i in range(200)]
    hc = cost_haircut(rets, fee_per_trade=0.0006)
    assert hc.net_sr < hc.gross_sr


# ---- claim router ----
def test_verify_claim_kinds():
    assert verify_claim("subset_win", p=0.009, n_tests=23)["survives"] is False
    assert verify_claim("model_gap", n=2000, p1=0.85, p2=0.80)["survives"] is True
    assert verify_claim("judge_bias", wins=68, n=100)["survives"] is False
    assert verify_claim("backtest", sr=0.12, T=250, n_trials=100)["survives"] is False


# ---- judge calibration ----
def test_calibrate_judge_detects_over_crediting():
    truth = [True, True, False, False, False, False, False]
    judge = [True] * 7
    r = calibrate_judge(judge, truth)
    assert r["over_credits"] == 5 and r["under_credits"] == 0
    assert not calibrate_judge(truth, truth)["biased"]


# ---- signed receipts ----
def test_receipt_verifies_and_detects_tamper():
    res = verify_claim("backtest", sr=0.12, T=250, n_trials=100)
    priv, pub = keypair()
    r = issue_receipt(res, priv, pub)
    assert verify_receipt(r, hmac_secret_hex=priv)
    r["payload"]["claim"]["survives"] = True
    assert not verify_receipt(r, hmac_secret_hex=priv)


# ---- metering ----
def test_credits_free_tier_then_payment_required(monkeypatch):
    store = Path(tempfile.mkdtemp()) / "ledger.json"
    monkeypatch.setattr(credits, "_STORE", store)
    k = "k1"
    for _ in range(credits.FREE_TIER_CALLS):
        assert credits.charge(k, "verify_backtest")["ok"]
    assert credits.charge(k, "verify_backtest").get("payment_required")
    credits.topup(k, 10)
    assert credits.charge(k, "verify_backtest")["ok"]


# ---- x402 ----
def test_x402_challenge_and_settlement():
    ch = x402.require_payment("verify_backtest", 0.03, "0xPayTo")
    assert ch["status"] == 402
    def ok(xp, c):
        return x402.Settlement(True, tx="0xabc", amount_units=int(c["accepts"][0]["maxAmountRequired"]))
    paid = x402.require_payment("verify_backtest", 0.03, "0xPayTo", x_payment="h", verifier=ok)
    assert paid["status"] == 200 and paid["paid"]
    assert x402.require_payment("verify_backtest", 0.03, "0xPayTo", x_payment="h")["status"] == 402


# ---- REST x402 layer ----
def test_rest_dev_mode_and_pricing():
    from starlette.testclient import TestClient
    from numguard.rest_api import app
    c = TestClient(app)
    assert c.get("/health").json()["ok"] is True
    assert "verify_backtest" in c.get("/pricing").json()["prices_usd"]
    # dev mode (no NUMGUARD_PAYTO in test env): runs free, real verdict
    r = c.post("/verify_backtest", json={"sr": 0.12, "T": 250, "n_trials": 100})
    assert r.status_code == 200 and r.json()["survives"] is False


def test_x402_facilitator_verifier_no_url_refuses():
    import os
    os.environ.pop("NUMGUARD_FACILITATOR_URL", None)
    v = x402.facilitator_verifier()
    s = v("some-x-payment", x402.payment_required("verify_backtest", 0.03, "0xPay"))
    assert not s.valid
