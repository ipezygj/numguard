"""rest_api — the same verification tools over HTTP, priced per-call with x402.

This is the wallet-native rail: an agent POSTs to an endpoint, gets an HTTP 402 with a machine-readable price
+ pay-to, pays USDC, retries with an `X-PAYMENT` header, and gets the result. Settlement goes through a real
x402 facilitator (env `NUMGUARD_FACILITATOR_URL`) to your receiving wallet (env `NUMGUARD_PAYTO`).

Config (env): NUMGUARD_PAYTO (your wallet, required to charge) · NUMGUARD_FACILITATOR_URL (an x402 facilitator)
· NUMGUARD_NETWORK (default 'base'). With PAYTO unset the endpoints run FREE (dev mode) so you can test.

Run:  uvicorn numguard.rest_api:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations
import hmac, json, os, time
from collections import deque, defaultdict

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import claims, judge as _judge, x402, _limits, identity, transparency, receipt as _rcpt
from . import backtest_battery as _battery

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
    if len(_hits) > 5000:                # bound the table: drop buckets with no hit inside the window
        for k in [k for k, v in list(_hits.items()) if not v or now - v[-1] > RATE_WINDOW]:
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
    if tool == "verify_backtest_series":
        _limits.check_list("returns", body.get("returns"))
        for nm in ("positions", "asset_returns", "turnover"):
            if body.get(nm) is not None:
                _limits.check_list(nm, body.get(nm))

try:                                   # optional: full leaderboard audit needs the evalgate library
    from evalgate import audit_matrix
except Exception:
    audit_matrix = None

PAYTO = os.environ.get("NUMGUARD_PAYTO", "")
NETWORK = os.environ.get("NUMGUARD_NETWORK", "base")
_ADMIN_KEY = os.environ.get("NUMGUARD_ADMIN_KEY", "")   # operator seed auth; unset => seeding DISABLED (fail closed)
# Prefer numguard's own on-chain settlement (NUMGUARD_GAS_KEY set) — one service, no facilitator. Fall back to
# an external x402 facilitator (NUMGUARD_FACILITATOR_URL) if that's how it's wired instead.
from . import settle
_VERIFIER = settle.self_verifier() or x402.facilitator_verifier()

def _issue_and_publish(b: dict) -> dict:
    """Verify a claim server-side, sign a receipt with numguard's issuer key, AND append it to the public
    transparency ledger. Returns the receipt + its inclusion (seq/hash) so the caller has both the portable
    proof and a pointer into the auditable record. numguard recomputes the verdict — it never signs a
    caller-supplied result."""
    kind, inputs = b["kind"], (b.get("inputs") or {})
    priv, pub = identity.issuer()
    verdict = claims.verify_claim(kind, **inputs)
    receipt = _rcpt.issue_receipt(verdict, priv, pub)
    entry = transparency.publish(receipt)
    return {"receipt": receipt, "log": {"seq": entry["seq"], "hash": entry["hash"], "prev": entry["prev"]}}


# path -> (tool name, price in USD, handler(body)->dict)
ROUTES = {
    "verify_backtest":   (0.03, lambda b: claims.verify_claim("backtest", **b)),
    "verify_subset_win": (0.02, lambda b: claims.verify_claim("subset_win", **b)),
    "verify_model_gap":  (0.02, lambda b: claims.verify_claim("model_gap", **b)),
    "verify_judge_bias": (0.02, lambda b: claims.verify_claim("judge_bias", **b)),
    "calibrate_judge":   (0.05, lambda b: _judge.calibrate_judge(b["judge_caught"], b["truth_caught"])),
    "audit_leaderboard": (0.05, lambda b: ({"verdict": str(audit_matrix(b["results"], n_boot=b.get("n_boot", 1000), seed=0))}
                                            if audit_matrix else {"error": "needs evalgate: pip install git+https://github.com/ipezygj/evalgate"})),
    # notary write-path: issue a signed receipt AND publish it to the public, hash-chained ledger
    "issue_receipt":     (0.05, _issue_and_publish),
    # full backtest-integrity battery on the actual returns series (leakage/HAC/PBO/drawdown/…)
    "verify_backtest_series": (0.05, lambda b: _battery.run_battery(
        b["returns"], positions=b.get("positions"), asset_returns=b.get("asset_returns"),
        turnover=b.get("turnover"), candidates=b.get("candidates"),
        periods_per_year=int(b.get("periods_per_year", 252)))),
}


def _client_ip(request) -> str:
    # The platform proxy (Render) APPENDS the real client IP as the LAST X-Forwarded-For entry; any earlier
    # entries are client-supplied and spoofable. Trust the rightmost one so a forged XFF can't mint a fresh
    # rate-limit bucket per request (which would defeat the limiter and grow the table unbounded).
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


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


# ----------------------------------------------------------------------------------------------------
# AGENT-PROOF layer (free, public, read-only): the reputation an agent can independently audit.
#   /pubkey                 the canonical issuer key that verifies EVERY numguard receipt
#   /.well-known/numguard.json  discovery doc (identity + where the proofs live)
#   /log/head               a signed fingerprint of the WHOLE verdict history (pin it)
#   /log/verify             recompute the hash chain — confirm nothing was rewritten
#   /receipts               browse the track record; each entry is a full, verifiable receipt
#   /receipts/{digest}      one verdict by its digest
# These are free on purpose: a reputation you have to pay to inspect is not a reputation.
def _rl_guard(request):
    return None if _rate_ok(_client_ip(request)) else JSONResponse(
        {"error": "rate limit exceeded, retry shortly"}, status_code=429)


def _ledger_error(e: Exception) -> JSONResponse:
    # the ledger backend (durable Turso, or the local file) failed — don't 500; surface a clean, safe
    # reason (the message never contains the token) so a misconfig is diagnosable, not opaque.
    return JSONResponse({"error": "ledger backend unavailable", "detail": str(e)[:200]}, status_code=503)


async def pubkey(request):
    return JSONResponse(identity.identity_card())


async def well_known(request):
    card = identity.identity_card()
    card["endpoints"] = {"pubkey": "/pubkey", "log_head": "/log/head", "log_verify": "/log/verify",
                         "receipts": "/receipts", "receipt_by_digest": "/receipts/{digest}",
                         "issue_receipt": "/issue_receipt (POST, paid)"}
    card["what"] = ("numguard signs every verdict (Ed25519) and appends it to a hash-chained public ledger. "
                    "Verify any receipt with the pubkey; audit the whole record with /log/verify.")
    return JSONResponse(card)


async def log_head(request):
    if (r := _rl_guard(request)) is not None:
        return r
    try:
        return JSONResponse(transparency.signed_head())
    except Exception as e:
        return _ledger_error(e)


async def log_verify(request):
    if (r := _rl_guard(request)) is not None:
        return r
    try:
        return JSONResponse(transparency.verify_log())
    except Exception as e:
        return _ledger_error(e)


async def receipts(request):
    if (r := _rl_guard(request)) is not None:
        return r
    try:
        offset = int(request.query_params.get("offset", 0))
        limit = int(request.query_params.get("limit", 50))
    except (TypeError, ValueError):
        return JSONResponse({"error": "offset/limit must be integers"}, status_code=400)
    try:
        return JSONResponse({"head": transparency.head(), "entries": transparency.entries(offset, limit)})
    except Exception as e:
        return _ledger_error(e)


async def receipt_by_digest(request):
    if (r := _rl_guard(request)) is not None:
        return r
    try:
        e = transparency.get(request.path_params["digest"])
    except Exception as ex:
        return _ledger_error(ex)
    return JSONResponse(e, status_code=200) if e else JSONResponse({"error": "not found"}, status_code=404)


def _admin_ok(request) -> bool:
    # fail CLOSED: with NUMGUARD_ADMIN_KEY unset, seeding is disabled entirely (never an open write path).
    supplied = request.headers.get("x-admin-key", "")
    return bool(_ADMIN_KEY) and hmac.compare_digest(supplied, _ADMIN_KEY)


async def admin_seed(request):
    """OPERATOR-ONLY: publish curated notary verdicts to the public ledger WITHOUT payment — how the
    operator seeds a public track record. Auth: X-ADMIN-KEY == NUMGUARD_ADMIN_KEY. Body is one claim
    {kind, inputs} or a batch {claims:[{kind,inputs},…]} (re-run your whole seed set after a redeploy,
    since the free-tier ledger is in-memory). Verdicts are recomputed server-side + signed with the
    canonical key, so a seeded verdict is exactly as verifiable as a paid one."""
    if not _admin_ok(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return JSONResponse({"error": "payload too large"}, status_code=413)
    try:
        body = json.loads(raw) if raw else {}
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    items = body.get("claims") if isinstance(body.get("claims"), list) else [body]
    if len(items) > 200:
        return JSONResponse({"error": "too many claims (max 200)"}, status_code=400)
    seeded = []
    for it in items:
        err, res = _run("issue_receipt", _issue_and_publish, it if isinstance(it, dict) else {})
        if err:
            return err
        seeded.append({"seq": res["log"]["seq"], "digest": res["receipt"]["digest"],
                       "kind": (it or {}).get("kind"),
                       "survives": res["receipt"]["payload"]["claim"]["survives"]})
    return JSONResponse({"seeded": len(seeded), "entries": seeded, "head": transparency.head()})


routes = [Route("/pricing", pricing), Route("/health", health),
          Route("/pubkey", pubkey), Route("/.well-known/numguard.json", well_known),
          Route("/log/head", log_head), Route("/log/verify", log_verify),
          Route("/receipts", receipts), Route("/receipts/{digest}", receipt_by_digest),
          Route("/admin/seed", admin_seed, methods=["POST"])]
routes += [Route(f"/{name}", _make(name, price, fn), methods=["POST"]) for name, (price, fn) in ROUTES.items()]
app = Starlette(routes=routes)
