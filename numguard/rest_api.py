"""rest_api — the same verification tools over HTTP, priced per-call with x402.

This is the wallet-native rail: an agent POSTs to an endpoint, gets an HTTP 402 with a machine-readable price
+ pay-to, pays USDC, retries with an `X-PAYMENT` header, and gets the result. Settlement goes through a real
x402 facilitator (env `NUMGUARD_FACILITATOR_URL`) to your receiving wallet (env `NUMGUARD_PAYTO`).

Config (env): NUMGUARD_PAYTO (your wallet, required to charge) · NUMGUARD_FACILITATOR_URL (an x402 facilitator)
· NUMGUARD_NETWORK (default 'base'). With PAYTO unset the endpoints run FREE (dev mode) so you can test.

Run:  uvicorn numguard.rest_api:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations
import json, os

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import claims, judge as _judge, x402
try:
    from evalgate import audit_matrix
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path.home() / "evalgate"))
    from evalgate import audit_matrix

PAYTO = os.environ.get("NUMGUARD_PAYTO", "")
NETWORK = os.environ.get("NUMGUARD_NETWORK", "base")
_VERIFIER = x402.facilitator_verifier()          # reads NUMGUARD_FACILITATOR_URL

# path -> (tool name, price in USD, handler(body)->dict)
ROUTES = {
    "verify_backtest":   (0.03, lambda b: claims.verify_claim("backtest", **b)),
    "verify_subset_win": (0.02, lambda b: claims.verify_claim("subset_win", **b)),
    "verify_model_gap":  (0.02, lambda b: claims.verify_claim("model_gap", **b)),
    "verify_judge_bias": (0.02, lambda b: claims.verify_claim("judge_bias", **b)),
    "calibrate_judge":   (0.05, lambda b: _judge.calibrate_judge(b["judge_caught"], b["truth_caught"])),
    "audit_leaderboard": (0.05, lambda b: {"verdict": str(audit_matrix(b["results"], n_boot=b.get("n_boot", 1000), seed=0))}),
}


def _make(tool: str, price: float, fn):
    async def handler(request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        # dev mode: no PAYTO configured -> run free
        if not PAYTO:
            out = fn(body)
            return JSONResponse({**out, "_billing": "free (NUMGUARD_PAYTO unset — dev mode)"})
        x_payment = request.headers.get("X-PAYMENT")
        gate = x402.require_payment(tool, price, PAYTO, x_payment=x_payment,
                                    verifier=_VERIFIER, network=NETWORK)
        if gate.get("status") == 402:
            return JSONResponse(gate, status_code=402)
        out = fn(body)
        resp = JSONResponse(out)
        resp.headers["X-PAYMENT-RESPONSE"] = json.dumps(gate.get("settlement", {}))
        return resp
    return handler


async def pricing(request):
    return JSONResponse({
        "network": NETWORK, "pay_to_configured": bool(PAYTO),
        "prices_usd": {k: v[0] for k, v in ROUTES.items()},
        "how": "POST to /<tool>; on 402 pay USDC per the 'accepts' block and retry with an X-PAYMENT header.",
    })


async def health(request):
    return JSONResponse({"ok": True, "service": "numguard", "paid": bool(PAYTO)})


routes = [Route("/pricing", pricing), Route("/health", health)]
routes += [Route(f"/{name}", _make(name, price, fn), methods=["POST"]) for name, (price, fn) in ROUTES.items()]
app = Starlette(routes=routes)
