"""calibrate_judge — is the LLM judge your agent trusts actually calibrated?

Agents grade other agents with LLM judges everywhere now. A judge you have not checked against ground truth
is a vibe. Feed this the judge's verdicts and the known-correct answers on a labelled slice; it returns how
often the judge agrees with truth and — the part that matters — whether its errors lean one direction (e.g.
systematically over-crediting, the failure mode behind length/self-preference bias).

Directional-bias significance uses evalgate.bias_rate. Same method as the pr-review-evals case study.
"""
from __future__ import annotations

from ._evalstats import bias_rate


def calibrate_judge(judge_caught, truth_caught) -> dict:
    """judge_caught / truth_caught: aligned booleans (or 0/1). truth = the ground-truth label,
    judge = whether the judge said 'good/caught'. Returns agreement + directional-bias verdict."""
    j = [bool(x) for x in judge_caught]
    t = [bool(x) for x in truth_caught]
    if len(j) != len(t) or not j:
        raise ValueError("need equal-length non-empty judge/truth lists")
    n = len(j)
    agree = sum(a == b for a, b in zip(j, t))
    over = sum(1 for a, b in zip(j, t) if a and not b)     # judge credited a real miss
    under = sum(1 for a, b in zip(j, t) if b and not a)    # judge failed a real catch
    n_dis = over + under
    bias = None
    biased = False
    direction = "none"
    if n_dis:
        b = bias_rate(over, n_dis, label="judge over-credits (of disagreements)")
        biased = b.biased
        bias = {"over": over, "under": under, "p_value": b.p_value}
        if biased:
            direction = "over-crediting (rewards confident/verbose answers that miss)" if over > under \
                else "under-crediting (penalises correct-but-differently-phrased answers)"
    return {
        "n": n, "agreement": agree, "agreement_rate": agree / n,
        "over_credits": over, "under_credits": under, "disagreements": n_dis,
        "biased": biased, "direction": direction, "bias": bias,
        "verdict": (f"judge agrees with truth on {agree}/{n} ({agree/n:.0%}); "
                    + ("no significant directional bias." if not biased
                       else f"significant bias — {direction} (p={bias['p_value']:.3g}). "
                            f"Trust it only where it agreed with truth.")),
    }


def _selftest():
    # a length-biased judge: over-credits every miss (like the pr-review-evals Sonnet case)
    truth = [True, True, False, False, False, False, False]   # 2 real catches, 5 misses
    judge = [True, True, True, True, True, True, True]         # judge says caught on everything
    r = calibrate_judge(judge, truth)
    assert r["over_credits"] == 5 and r["under_credits"] == 0
    # a well-calibrated judge
    r2 = calibrate_judge(truth, truth)
    assert r2["agreement_rate"] == 1.0 and not r2["biased"]
    print("judge selftest: OK (over-credit bias detected; perfect judge clean)")


if __name__ == "__main__":
    _selftest()
