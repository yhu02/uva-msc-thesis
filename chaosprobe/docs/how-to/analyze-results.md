# How to analyze results

Every analysis command consumes the `summary.json` written by `chaosprobe run`.
You can try them all against the bundled fixture in `examples/` with **no
cluster** — substitute your own `summary.json` for real runs.

For what each statistic *means* and *why* it's used, see
[Concepts](../explanation/concepts.md) and [`../../TECHNICAL.md`](../../TECHNICAL.md).

## Check data quality first

```bash
uv run chaosprobe doctor -s examples/example-summary.json
uv run chaosprobe doctor -s examples/example-summary.json --strict   # exit 1 on warnings
```

Flags tainted iterations, low placement match, OOM kills, missing recovery,
inconclusive CIs, schema drift, and missing run metadata — run it before you
trust any numbers.

## Summarize per strategy

```bash
uv run chaosprobe summarize -s examples/example-summary.json
uv run chaosprobe summarize -s examples/example-summary.json --strategy spread
```

Per-strategy aggregate roll-up: resilience, the recovery split, coefficient of
variation, and a histogram.

## Compare strategies statistically

```bash
uv run chaosprobe stats -s examples/example-summary.json --metric resilience
uv run chaosprobe stats -s examples/example-summary.json --all-metrics --markdown
uv run chaosprobe stats -s examples/example-summary.json --baseline spread
uv run chaosprobe stats -s examples/example-summary.json --effect-size-min medium
uv run chaosprobe stats -s run1.json --merge run2.json --merge run3.json   # pool samples
```

Bootstrap confidence intervals for per-strategy means, plus pairwise
Mann-Whitney U (Holm-Bonferroni corrected) with Cliff's-delta effect sizes.

## Check statistical power

```bash
uv run chaosprobe power -s examples/example-summary.json --metric resilience
```

How many iterations you'd need to detect a target effect — answers "is n=3
enough?".

## Get a recommendation

```bash
uv run chaosprobe recommend -s examples/example-summary.json
uv run chaosprobe recommend -s examples/example-summary.json --metric recovery
uv run chaosprobe recommend -s examples/example-summary.json --alpha 0.01 --json
```

The `baseline` control (which injects no real fault) is **excluded by default** —
it isn't a deployable placement and its score is a no-chaos artifact. Pass
`--include-control` to keep it in the ranking as a reference.

Ranks the strategies and renders a verdict — `significant` (the leader provably
beats the runner-up), `tentative` (leads but not significantly — collect more
iterations), `single-strategy`, or `no-data`.

When the summary covers **more than one fault** (a multi-fault matrix run with
several `-e` experiments), `recommend` ranks each fault class **separately** —
comparing placements only against others tested under the *same* fault — and
emits one recommendation per fault (a `byFault` map under `--json`, one
`Fault: <name>` section in text). Single-fault summaries keep the flat output.

## Drill into a single iteration

```bash
uv run chaosprobe inspect -s examples/example-summary.json --strategy colocate -i 3
uv run chaosprobe inspect -s examples/example-summary.json --strategy spread -i 1 --json
```

Per-iteration record: verdict, probe results, recovery split, and cluster
snapshots.

## Check run-to-run stability

```bash
uv run chaosprobe diff --a baseline.json --b rerun.json
uv run chaosprobe diff --a baseline.json --b rerun.json --strict   # exit 1 on disjoint CIs
```

## Export for downstream ML

```bash
uv run chaosprobe export -s examples/example-summary.json -o iters.csv
uv run chaosprobe export -s examples/example-summary.json --format jsonl -o iters.jsonl
```

Flattens iterations to CSV / JSONL. (For aligned, labeled time-series from
Neo4j, use [`ml-export`](../reference/cli.md#ml-export) instead.)

## One-shot appendix report

```bash
uv run chaosprobe report -s examples/example-summary.json -o report.md
uv run chaosprobe report -s rerun.json --diff baseline.json -o report.md
```

Bundles `doctor` + `summarize` + `stats` (+ optional `diff`) into a single
markdown appendix.

## Compare during-load route tails (contention runs)

For a `load-contention` run (driven with `--load-profile spike`), the metric is
during-load route tail latency per placement, not the resilience score:

```bash
uv run python scripts/contention_routes.py -s <run>/summary.json
```

This reads `aggregated.routeViewAggregate` and compares the during-load route
p95 across strategies (e.g. `colocate` vs `spread`) to surface the east-west
inter-service locality effect.

To check *why* a placement has that east-west penalty, compute its **cross-node
call fraction** — the fraction of inter-service edges whose endpoints sit on
different nodes, from the actual per-iteration `podPlacements` + the dependency
edges in `routeViewAggregate`:

```bash
uv run python scripts/cross_node_fraction.py -s <run>/summary.json
```

It prints each strategy's cross-node fraction next to its east-west p95 and the
rank correlation between them (the hypothesised placement → fraction → tail
chain). Note: a *gradient* needs the intermediate-fraction strategies
(`dependency-aware`, `best-fit`, `random`, `adversarial`) in the run — with only
`colocate` forcing node-locality the spreading strategies tie, and the script
says so.

## Node failure: placement × replicas interaction (E1)

For `node-drain` runs across two replica counts (e.g. `run -r 1` and `run -r 3`),
test whether placement is a *user-visible* availability lever and whether the
effect interacts with replica count:

```bash
uv run python scripts/node_drain_interaction.py --results-dir results
```

It derives, per run and strategy, the trough availability (mean
`ready_trough / ready_pre` over the measured services) and the replica count
(from the pre-chaos ready count), then runs an **Aligned Rank Transform
factorial ANOVA** (`art_anova`) over `placement × replicas`. The headline is the
**interaction** term: a significant interaction means placement moves
availability at multiple replicas but not at one. With only one replica level in
the results, the replica and interaction effects are reported as `n/a`.

## Stronger statistics in the hypothesis scripts

The H1–H3 scripts above back their claims with the tests in
`chaosprobe.metrics.statistics`:

- `score_variance.py` prints a **bootstrap 95% CI** on `ICC_strategy`
  (`icc_bootstrap`), so the "the score can't rank placements" claim carries its
  uncertainty.
- `mechanism_metrics.py` adds a **paired Wilcoxon signed-rank** test and an exact
  **sign test** for the spread-vs-colocate flush comparison (`wilcoxon_signed_rank`),
  turning the "k/k runs" count into a p-value.
- `h3_mechanism_outcome.py` reports a **TOST equivalence** verdict
  (`tost_equivalence_correlation`): "decoupled (TOST)" means the dependent-route
  correlation is statistically *inside* ±0.3 — evidence of absence, not absence
  of evidence.

## Track a multi-session campaign

A single session over-states the score's discriminating power (with one run the
run-to-run variance component is structurally zero, so `ICC_strategy` looks
several-fold larger than it is). Run independent sessions into a dedicated
`campaign-results/` directory (`run ... -o campaign-results`, one timestamped run
dir per session) and watch them accumulate:

```bash
uv run python scripts/campaign_status.py --results-dir campaign-results
```

It reports, across all clean sessions: **H1** `ICC_strategy` with its bootstrap CI
(falling toward its true value as run-to-run variance becomes visible), **H2** the
spread-vs-colocate conntrack flush paired by session (Wilcoxon + exact sign test —
significant around N≥6), and an **H7** probe of whether the flush tracks a
cross-node fraction, comparing the *global* graph fraction against the
*target-scoped* one (edges incident on the chaos victim, via
`target_scoped_cross_node_fraction`). It ends with a blunt sufficiency gate
(how many more sessions are needed for a significant sign test).

## Next

- Full flags for each command: [CLI reference](../reference/cli.md).
- The statistics, explained: [Concepts → statistics](../explanation/concepts.md).
