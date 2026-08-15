# A worked panel, with the answer in the box

Fifty strategies were tried. Three of them are genuinely skilled and forty-seven
are noise. You know that here, which is the point: a hurdle can be scored rather
than admired.

```bash
python -m numguard.fdr examples/returns_50_strategies.csv \
    --truth examples/returns_50_strategies.truth.txt
```

```
50 strategies, 240 periods each
  |t| range           0.01 .. 3.85

  Bonferroni 5%       |t| >= 3.29   -> 1 discoveries
                       finds 1/3 skilled, 0 false

  FDR hurdle          |t| >= 3.40   -> 1 discoveries   (target FDR 0.05, expected false 0.05)
                       finds 1/3 skilled, 0 false
```

Bonferroni pays for its safety with two missed strategies out of three, and it
never tells you that is the price. The whole argument of Harvey & Liu is that
the price is a choice, so state it:

```bash
python -m numguard.fdr examples/returns_50_strategies.csv \
    --truth examples/returns_50_strategies.truth.txt \
    --criterion oratio --target 0.1
```

```
  Harvey & Liu double bootstrap, criterion oratio <= 0.1
        p0   hurdle   TYPE1   TYPE2   ORATIO  disc  skilled  false
     0.000     0.00   1.000   0.000     0.00    50        3/3     47   <- degenerate: p0 assumes nothing is real
     0.005     0.00   1.000   0.000     0.00    50        3/3     47   <- degenerate: p0 assumes nothing is real
     0.020     2.35   0.350   0.002     0.09     3        3/3      0
     0.050     2.90   0.084   0.013     0.09     1        1/3      0
     0.100     2.85   0.071   0.056     0.09     1        1/3      0
     0.150     2.65   0.094   0.103     0.10     1        1/3      0
     0.200     2.55   0.104   0.137     0.09     1        1/3      0
```

At `p0 = 0.02` the hurdle falls to `|t| >= 2.35` and recovers all three, with no
false positives on this panel.

Do not read that as "assume less and find more". The true fraction here is
`3/50 = 0.06`, and the row nearest it, `p0 = 0.05`, recovers only 1 of 3 at
`|t| >= 2.90`. The row that performs best is not the row whose assumption is
most nearly right, and the hurdle across the grid is not monotone in `p0`
(2.35, 2.90, 2.85, 2.65, 2.55). One panel of fifty is far too small to say
whether that pattern means anything; it is reported here because hiding it
would be worse.

The lesson that does survive is the uncomfortable one: **`p0` is an input, and
the answer moves with it.** `hurdle_curve` prints the whole grid for exactly
that reason, so a single number cannot travel without the assumption that
produced it.

The first two rows are labelled degenerate because they are. If you assume
nothing in the panel is real, nothing can be missed, any criterion about misses
is satisfied at a hurdle of zero, and all fifty strategies "survive". A pass you
get by assuming the question away is not a pass.

## Your own data

One column per strategy you actually ran, one row per period. A header row of
names is optional.

```csv
momentum_1,momentum_2,value_1
0.0123,-0.0044,0.0067
...
```

The whole panel matters, not the winner: the hurdle comes from the multiplicity
you really faced, so trials you ran and discarded belong in the file.

```
--target      the level for the criterion (default 0.05)
--criterion   fdr (default) or oratio
--p0          assumed fraction genuinely skilled; omit for the whole curve
--truth       file of the genuinely skilled column names, to score the answer
--seed        default 0; the bootstrap is seeded, so runs are reproducible
```

## Regenerating

`returns_50_strategies.csv` is produced by `make_panel.py`, seeded, so it can be
rebuilt exactly:

```bash
python examples/make_panel.py
```

The three planted strategies sit at column indices spaced through the panel
rather than at the front, so a reader cannot pattern-match the answer from the
column order. Their drifts are set in t units: one clear of the Bonferroni
cutoff and two below it, which is what makes the miss visible. Those are targets
for the expected t, not the realised one; the drawn series land where they land.
