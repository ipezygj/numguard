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


# ---- hardening ----
def test_hardening_rejects_oversized_and_bad_input(monkeypatch):
    import os
    monkeypatch.delenv("NUMGUARD_PAYTO", raising=False)
    import importlib
    from numguard import rest_api
    importlib.reload(rest_api)
    from starlette.testclient import TestClient
    c = TestClient(rest_api.app)
    # oversized list -> 400 (not a CPU-pinning run)
    assert c.post("/calibrate_judge", json={"judge_caught": [True]*6000, "truth_caught": [True]*6000}).status_code == 400
    # missing required field -> clean 400, never a 500 traceback
    assert c.post("/verify_backtest", json={}).status_code == 400
    # valid still works
    assert c.post("/verify_backtest", json={"sr": 0.12, "T": 250, "n_trials": 100}).status_code == 200


def test_rate_limit_returns_429(monkeypatch):
    import importlib
    from numguard import rest_api
    importlib.reload(rest_api)
    from starlette.testclient import TestClient
    c = TestClient(rest_api.app)
    hit_429 = any(c.post("/verify_model_gap", json={"n": 100, "p1": 0.8, "p2": 0.7}).status_code == 429
                  for _ in range(rest_api.RATE_LIMIT + 5))
    assert hit_429


def test_mcp_rail_guards_oversized_input_without_billing():
    """The MCP tool reaches the same compute as REST — an oversized payload must be rejected on that rail
    too, before any credit is charged."""
    from numguard import mcp_server as m, credits
    cj = getattr(m.calibrate_judge, "fn", m.calibrate_judge)
    al = getattr(m.audit_leaderboard, "fn", m.audit_leaderboard)
    r1 = cj("mcp-guard-key", [True] * 6000, [False] * 6000)
    r2 = al("mcp-guard-key", {"a": list(range(6000))})
    assert "error" in r1 and "error" in r2
    # rejected before billing: the fresh key keeps its full free tier
    assert credits.balance("mcp-guard-key")["free_remaining"] == credits.FREE_TIER_CALLS
