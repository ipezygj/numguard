"""proof_gallery — numguard verifying real numbers, with a receipt you can check.

This is dogfooding, not decoration: a set of *illustrative but honest* cases — canonical failure modes and
genuine signals — run through numguard's REAL checks, each producing a REAL signed receipt. Nothing here is
fabricated usage; every verdict is what the checker actually returns, and every receipt verifies offline
against the embedded key. The point is to let an agent (or a human) SEE the product discriminate: it accepts
real signal and rejects the fake — the exact thing it exists to do.

Run:  python examples/proof_gallery.py
Emits: docs/proof_gallery.json  (machine-readable bundle, receipts included)
       docs/PROOF_GALLERY.md    (readable gallery + "verify it yourself")
"""
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from numguard import claims, receipt, receipt_spec

rng = random.Random(20260724)


def _leakage_series(n=750):
    """A look-ahead fraud: position = sign of the SAME bar's return. Gorgeous Sharpe, pure fiction."""
    asset = [rng.gauss(0, 0.01) for _ in range(n)]
    return {"returns": [(1 if asset[t] > 0 else -1) * asset[t] for t in range(n)],
            "positions": [1.0 if asset[t] > 0 else -1.0 for t in range(n)],
            "asset_returns": asset}


# Each case: the number someone might assert, the honest source/note, and the check numguard runs.
# A deliberate mix of survives + fails — a gallery that only ever rejects is a hit-piece, not a referee.
CASES = [
    {
        "id": "overfit-sharpe",
        "title": "A Sharpe of 0.12/period that was the best of 100 configs",
        "claim": "\"Our strategy Sharpes well — 0.12 per period over 250 periods.\"",
        "note": "Canonical selection bias: at one hypothesis it looks fine, but it was the best of 100 tried. "
                "The Deflated Sharpe (Bailey & Lopez de Prado, 2014) discounts the search and it collapses.",
        "kind": "backtest", "inputs": {"sr": 0.12, "T": 250, "n_trials": 100},
    },
    {
        "id": "real-edge",
        "title": "A single hypothesis, long sample, Sharpe 0.15",
        "claim": "\"One idea, pre-registered, tested once on 1000 periods: Sharpe 0.15.\"",
        "note": "Nothing to deflate — one trial, a long track record. A genuine edge should, and does, survive.",
        "kind": "backtest", "inputs": {"sr": 0.15, "T": 1000, "n_trials": 1},
    },
    {
        "id": "real-model-gap",
        "title": "Model A beats B by 5 points over 2000 items each",
        "claim": "\"A is better: 85% vs 80% on 2000 held-out items.\"",
        "note": "A 5-point gap at n=2000 sits above the minimum detectable effect — a real ranking, not noise.",
        "kind": "model_gap", "inputs": {"n": 2000, "p1": 0.85, "p2": 0.80},
    },
    {
        "id": "noise-model-gap",
        "title": "Model A beats B by 1 point over 200 items each",
        "claim": "\"A wins: 85% vs 84% — ship A.\"",
        "note": "A 1-point gap at n=200 is below the MDE: indistinguishable at that sample. Not a ranking.",
        "kind": "model_gap", "inputs": {"n": 200, "p1": 0.85, "p2": 0.84},
    },
    {
        "id": "cherry-picked-subset",
        "title": "\"We win on subset X, p=0.03\" — best of 20 subsets",
        "claim": "\"Significant advantage on subset X (p=0.03).\"",
        "note": "The p looks significant until you count the family: it was the best of 20 subsets. After "
                "multiple-testing correction it's within noise.",
        "kind": "subset_win", "inputs": {"p": 0.03, "n_tests": 20},
    },
    {
        "id": "judge-prefers-ours",
        "title": "An LLM judge prefers our answer 68/100",
        "claim": "\"The judge picks ours 68% of the time — clearly better.\"",
        "note": "68/100 is significantly above chance — but a judge preference this size often tracks length, "
                "position, or same-family style, not quality. Flagged as bias to investigate.",
        "kind": "judge_bias", "inputs": {"wins": 68, "n": 100},
    },
    {
        "id": "judge-fair",
        "title": "A judge splits 54/100",
        "claim": "\"Ours wins 54% of head-to-heads.\"",
        "note": "54/100 is indistinguishable from a coin flip — no evidence of bias, and no evidence of an edge.",
        "kind": "judge_bias", "inputs": {"wins": 54, "n": 100},
    },
    {
        "id": "look-ahead-fraud",
        "title": "A backtest positioned on the same bar it trades",
        "claim": "\"The full series backtests beautifully.\"",
        "note": "The integrity battery reads the actual returns: positions are taken on the same bar's move — "
                "look-ahead leakage. A Sharpe built from time the strategy never had. Flagged CRITICAL.",
        "kind": "backtest_series", "inputs": _leakage_series(),
    },
]


def build():
    priv, pub = receipt.keypair()
    entries = []
    for c in CASES:
        verdict = claims.verify_claim(c["kind"], **c["inputs"])
        rc = receipt.issue_receipt(verdict, priv, pub)
        assert receipt_spec.verify_any(rc)["valid"] is True, f"gallery receipt must verify: {c['id']}"
        # keep the bundle readable: the leakage case carries huge input arrays — summarize them
        shown_inputs = c["inputs"]
        if c["kind"] == "backtest_series":
            shown_inputs = {"returns": f"<{len(c['inputs']['returns'])} periods>",
                            "positions": "<same-bar sign of asset return>",
                            "asset_returns": f"<{len(c['inputs']['asset_returns'])} periods>"}
        entries.append({
            "id": c["id"], "title": c["title"], "claim": c["claim"], "note": c["note"],
            "kind": c["kind"], "inputs": shown_inputs,
            "survives": bool(verdict.get("survives")),
            "verdict": verdict.get("verdict") or verdict.get("summary")
                       or ("survives" if verdict.get("survives") else "flagged"),
            "risk": verdict.get("risk"), "flags": verdict.get("flags"),
            "receipt": rc,
        })
    return pub, entries


def write_json(pub, entries, path):
    bundle = {
        "spec": receipt_spec.SPEC,
        "issuer_public_key": pub,
        "about": "numguard verifying real numbers. Every verdict is what the checker returns; every receipt "
                 "verifies offline against issuer_public_key. Illustrative cases, honest results.",
        "verify": "for any case: numguard.receipt_spec.verify_any(case['receipt']) -> {'valid': True, ...}",
        "cases": entries,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2)


def write_md(pub, entries, path):
    surv = sum(1 for e in entries if e["survives"])
    lines = [
        "# Proof gallery — numguard on real numbers",
        "",
        "> Dogfooding, not decoration. Each row is a number someone might assert, run through numguard's **real**",
        "> check, with a **real** signed receipt. Nothing here is fabricated usage — the verdicts are what the",
        f"> checker actually returns. Of {len(entries)} cases it lets **{surv} through** and **flags {len(entries)-surv}**.",
        "> That discrimination is the product: it accepts real signal and rejects the fake.",
        "",
        f"Issuer public key (verify any receipt offline against this): `{pub}`",
        "",
        "| # | The asserted number | Check | Verdict |",
        "|---|---|---|---|",
    ]
    for i, e in enumerate(entries, 1):
        mark = "✅ survives" if e["survives"] else "🚫 flagged"
        lines.append(f"| {i} | {e['title']} | `{e['kind']}` | {mark} |")
    lines += ["", "---", ""]
    for i, e in enumerate(entries, 1):
        mark = "✅ **survives**" if e["survives"] else "🚫 **flagged**"
        lines += [
            f"### {i}. {e['title']}  — {mark}",
            "",
            f"*The claim:* {e['claim']}",
            "",
            f"*Why it's here:* {e['note']}",
            "",
            f"*numguard's verdict:* {e['verdict']}",
        ]
        if e.get("risk"):
            lines.append(f"  \n*Risk:* `{e['risk']}`" + (f" — flags: `{e['flags']}`" if e.get("flags") else ""))
        lines += [
            "",
            f"*Receipt digest:* `{e['receipt']['digest']}`  \n"
            f"*Verify it yourself:* `receipt_spec.verify_any(<this receipt>)` → `valid: true` "
            "(offline, issuer-agnostic — you need nothing but the receipt).",
            "",
        ]
    lines += [
        "---",
        "",
        "## Verify the whole gallery yourself",
        "",
        "```bash",
        "pip install numguard",
        "python -c \"import json; from numguard import receipt_spec as S; \\",
        "  b=json.load(open('docs/proof_gallery.json')); \\",
        "  print(all(S.verify_any(c['receipt'])['valid'] for c in b['cases']), 'all receipts verify')\"",
        "```",
        "",
        "Every receipt is signed by the key above and checks out against it — no numguard account, no network.",
        "That's the whole idea: don't believe the gallery, *verify* it.",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    docs = os.path.join(ROOT, "docs")
    os.makedirs(docs, exist_ok=True)
    pub, entries = build()
    write_json(pub, entries, os.path.join(docs, "proof_gallery.json"))
    write_md(pub, entries, os.path.join(docs, "PROOF_GALLERY.md"))
    surv = sum(1 for e in entries if e["survives"])
    print(f"proof gallery: {len(entries)} cases ({surv} survive, {len(entries)-surv} flagged), "
          f"all receipts verify. Wrote docs/proof_gallery.json + docs/PROOF_GALLERY.md")
