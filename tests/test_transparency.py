"""Tests for the agent-proof layer: the public issuer identity + hash-chained verdict ledger.

These prove the property that makes numguard's reputation machine-auditable: an agent can fetch ONE
canonical key, verify any verdict with it, and confirm the whole history was never rewritten.
"""
import json
import os
import tempfile

import pytest


@pytest.fixture()
def isolated(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("NUMGUARD_TRANSPARENCY", os.path.join(d, "t.jsonl"))
    monkeypatch.setenv("NUMGUARD_ISSUER", os.path.join(d, "issuer.json"))
    monkeypatch.delenv("NUMGUARD_PAYTO", raising=False)      # free/dev mode
    monkeypatch.delenv("NUMGUARD_ISSUER_KEY", raising=False)
    import importlib
    from numguard import transparency, identity
    importlib.reload(identity)
    importlib.reload(transparency)
    return transparency, identity


def test_chain_verifies_and_tamper_is_detected(isolated):
    transparency, _ = isolated
    from numguard import receipt as R
    priv, pub = R.keypair()
    for i in range(4):
        r = R.issue_receipt({"claim": {"kind": "backtest", "survives": bool(i % 2)}}, priv, pub)
        transparency.publish(r, ts=1000 + i)
    assert transparency.verify_log()["ok"]
    assert transparency.head()["count"] == 4
    # rewrite a past entry -> the chain must break exactly there
    store = os.environ["NUMGUARD_TRANSPARENCY"]
    lines = open(store, encoding="utf-8").read().splitlines()
    e = json.loads(lines[2]); e["digest"] = "00" * 32
    lines[2] = json.dumps(e, separators=(",", ":"))
    open(store, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    bad = transparency.verify_log()
    assert not bad["ok"] and bad["bad_seq"] == 2


def test_every_ledger_receipt_verifies_with_the_published_pubkey(isolated):
    transparency, identity = isolated
    from numguard import receipt as R
    priv, pub = identity.issuer()
    for i in range(3):
        transparency.publish(R.issue_receipt({"claim": {"kind": "backtest", "survives": True}}, priv, pub))
    card = identity.identity_card()
    assert card["public_verifiable"] and len(card["public_key"]) == 64
    for e in transparency.entries(0, 10):
        # an agent has ONLY the receipt + the published pubkey — that must be enough
        assert e["receipt"]["public_key"] == card["public_key"]
        assert R.verify_receipt(e["receipt"])


def test_signed_head_commits_to_the_whole_history(isolated):
    transparency, _ = isolated
    from numguard import receipt as R
    priv, pub = None, None
    from numguard import identity
    priv, pub = identity.issuer()
    transparency.publish(R.issue_receipt({"claim": {"kind": "backtest", "survives": True}}, priv, pub))
    sh = transparency.signed_head()
    assert R.verify_receipt(sh["receipt"])
    assert sh["receipt"]["payload"]["detail"]["log_head"]["head_hash"] == transparency.head()["head_hash"]


def test_http_agent_proof_flow(isolated):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    import importlib
    from numguard import rest_api
    importlib.reload(rest_api)                 # pick up the isolated env + free mode
    from numguard import receipt as R
    c = TestClient(rest_api.app)
    r = c.post("/issue_receipt", json={"kind": "backtest", "inputs": {"sr": 0.12, "T": 250, "n_trials": 100}})
    assert r.status_code == 200, r.text
    pk = c.get("/pubkey").json()
    feed = c.get("/receipts").json()
    assert feed["head"]["count"] >= 1
    for e in feed["entries"]:
        assert e["receipt"]["public_key"] == pk["public_key"]
        assert R.verify_receipt(e["receipt"])
    assert c.get("/log/verify").json()["ok"]
    assert R.verify_receipt(c.get("/log/head").json()["receipt"])


def test_admin_seed_is_authed_and_fail_closed(isolated, monkeypatch):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    import importlib
    from numguard import receipt as R

    # with an admin key set, only the right key can seed
    monkeypatch.setenv("NUMGUARD_ADMIN_KEY", "operator-secret-xyz")
    from numguard import rest_api
    importlib.reload(rest_api)
    c = TestClient(rest_api.app)
    claim = {"kind": "backtest", "inputs": {"sr": 0.15, "T": 1000, "n_trials": 1}}
    assert c.post("/admin/seed", json=claim).status_code == 401                         # no key
    assert c.post("/admin/seed", headers={"X-ADMIN-KEY": "nope"}, json=claim).status_code == 401  # wrong
    ok = c.post("/admin/seed", headers={"X-ADMIN-KEY": "operator-secret-xyz"}, json=claim)
    assert ok.status_code == 200 and ok.json()["seeded"] == 1
    # the seeded verdict is a normal, publicly-verifiable ledger entry
    feed = c.get("/receipts").json()
    assert feed["head"]["count"] == 1 and R.verify_receipt(feed["entries"][0]["receipt"])

    # fail CLOSED: with no admin key configured, seeding is disabled entirely
    monkeypatch.setenv("NUMGUARD_ADMIN_KEY", "")
    importlib.reload(rest_api)
    c2 = TestClient(rest_api.app)
    assert c2.post("/admin/seed", headers={"X-ADMIN-KEY": ""}, json=claim).status_code == 401


def test_durable_turso_backend(monkeypatch):
    """The Turso durable path (used on a diskless host so the ledger survives redeploys): same hash-chain,
    same verifiable receipts, exercised against an in-memory fake of the Turso HTTP API."""
    import importlib
    monkeypatch.setenv("NUMGUARD_TURSO_URL", "libsql://fake.turso.io")
    monkeypatch.setenv("NUMGUARD_TURSO_TOKEN", "faketoken")
    from numguard import transparency as T, receipt as R, identity
    importlib.reload(identity)
    importlib.reload(T)
    assert T._turso_on()
    db, cols = [], ("seq", "ts", "digest", "kind", "survives", "public_key", "prev", "hash", "receipt")

    def fake_turso(sql, args=(), _ensure=True):
        s = sql.strip().upper()
        if s.startswith("INSERT"):
            db.append(dict(zip(cols, args)))
            return [], 1
        if "ORDER BY SEQ DESC" in s:
            return ([db[-1]] if db else []), 0
        if "WHERE DIGEST" in s:
            return [r for r in db if r["digest"] == args[0]], 0
        return list(db), 0

    monkeypatch.setattr(T, "_turso", fake_turso)
    priv, pub = identity.issuer()
    for i in range(3):
        T.publish(R.issue_receipt({"claim": {"kind": "backtest", "survives": bool(i % 2)}}, priv, pub), ts=1000 + i)
    v = T.verify_log()
    assert v["ok"] and v["count"] == 3
    feed = T.entries(0, 10)
    assert [e["seq"] for e in feed] == [2, 1, 0]
    assert all(R.verify_receipt(e["receipt"]) for e in feed)
    assert T.head()["head_hash"] == v["head_hash"]
