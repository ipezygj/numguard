"""rest_api — the same verification tools over HTTP, priced per-call with x402.

This is the wallet-native rail: an agent POSTs to an endpoint, gets an HTTP 402 with a machine-readable price
+ pay-to, pays USDC, retries with an `X-PAYMENT` header, and gets the result. Settlement goes through a real
x402 facilitator (env `NUMGUARD_FACILITATOR_URL`) to your receiving wallet (env `NUMGUARD_PAYTO`).

Config (env): NUMGUARD_PAYTO (your wallet, required to charge) · NUMGUARD_FACILITATOR_URL (an x402 facilitator)
· NUMGUARD_NETWORK (default 'base'). With PAYTO unset the endpoints run FREE (dev mode) so you can test.

Run:  uvicorn numguard.rest_api:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations
import json, os, time
from collections import deque, defaultdict

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import claims, judge as _judge, x402, _limits

# ---- hardening limits (a public, real-money endpoint) ----
MAX_ITEMS = _limits.MAX_ITEMS   # cap list/matrix sizes so a huge payload can't pin the CPU
MAX_NBOOT = _limits.MAX_NBOOT   # cap leaderboard bootstrap iterations (shared with the MCP rail)
MAX_BODY_BYTES = 256 * 1024
RATE_LIMIT, RATE_WINDOW = 60, 60.0     # 60 requests / 60s per client IP
_hits: dict = defaultdict(deque)


def _rate_ok(ip: str) -> bool:
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        return False
    q.append(now)
    if len(_hits) > 10000:               # bound the table
        for k in [k for k, v in list(_hits.items()) if not v][:5000]:
            _hits.pop(k, None)
    return True


def _guard_body(tool: str, body: dict) -> None:
    """Reject inputs that could exhaust CPU/memory before doing any work. Raises ValueError."""
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    if tool == "calibrate_judge":
        _limits.check_list("judge_caught", body.get("judge_caught"))
        _limits.check_list("truth_caught", body.get("truth_caught"))
    if tool == "audit_leaderboard":
        _limits.check_leaderboard(body.get("results", {}), body.get("n_boot", 1000))

try:                                   # optional: full leaderboard audit needs the evalgate library
    from evalgate import audit_matrix
except Exception:
    audit_matrix = None

PAYTO = os.environ.get("NUMGUARD_PAYTO", "")
NETWORK = os.environ.get("NUMGUARD_NETWORK", "base")
# Prefer numguard's own on-chain settlement (NUMGUARD_GAS_KEY set) — one service, no facilitator. Fall back to
# an external x402 facilitator (NUMGUARD_FACILITATOR_URL) if that's how it's wired instead.
from . import settle
_VERIFIER = settle.self_verifier() or x402.facilitator_verifier()

# path -> (tool name, price in USD, handler(body)->dict)
ROUTES = {
    "verify_backtest":   (0.03, lambda b: claims.verify_claim("backtest", **b)),
    "verify_subset_win": (0.02, lambda b: claims.verify_claim("subset_win", **b)),
    "verify_model_gap":  (0.02, lambda b: claims.verify_claim("model_gap", **b)),
    "verify_judge_bias": (0.02, lambda b: claims.verify_claim("judge_bias", **b)),
    "calibrate_judge":   (0.05, lambda b: _judge.calibrate_judge(b["judge_caught"], b["truth_caught"])),
    "audit_leaderboard": (0.05, lambda b: ({"verdict": str(audit_matrix(b["results"], n_boot=b.get("n_boot", 1000), seed=0))}
                                            if audit_matrix else {"error": "needs evalgate: pip install git+https://github.com/ipezygj/evalgate"})),
}


def _client_ip(request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    return (xff.split(",")[0].strip() if xff else (request.client.host if request.client else "unknown"))


def _run(tool: str, fn, body: dict):
    """Validate + run a tool, mapping bad input to a clean 400 and never leaking a traceback."""
    try:
        _guard_body(tool, body)
        return None, fn(body)
    except (ValueError, KeyError, TypeError) as e:
        return JSONResponse({"error": f"bad request: {e}"}, status_code=400), None
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500), None


def _make(tool: str, price: float, fn):
    async def handler(request):
        if not _rate_ok(_client_ip(request)):
            return JSONResponse({"error": "rate limit exceeded, retry shortly"}, status_code=429)
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return JSONResponse({"error": "payload too large"}, status_code=413)
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        # dev mode: no PAYTO configured -> run free
        if not PAYTO:
            err, out = _run(tool, fn, body)
            return err or JSONResponse({**out, "_billing": "free (NUMGUARD_PAYTO unset — dev mode)"})
        x_payment = request.headers.get("X-PAYMENT")
        gate = x402.require_payment(tool, price, PAYTO, x_payment=x_payment,
                                    verifier=_VERIFIER, network=NETWORK)
        if gate.get("status") == 402:
            return JSONResponse(gate, status_code=402)
        err, out = _run(tool, fn, body)
        if err:
            return err
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
