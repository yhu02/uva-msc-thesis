# Contributing

This is an MSc thesis project — primarily maintained as a research artifact rather than open-source software. Bug reports, reproductions, and methodology questions are welcome.

## Development setup

```bash
git clone git@github.com:yhu02/uva-msc-thesis.git
cd uva-msc-thesis/chaosprobe
uv sync
```

Required: Python 3.10+, [`uv`](https://docs.astral.sh/uv/), `kubectl`, `helm`. A Kubernetes cluster is only needed to actually run experiments — unit tests don't need one.

## Branch + commit hygiene

- Branch off `main` for every change. Use a conventional-commit prefix in both branch and commit names: `feat/`, `fix/`, `docs/`, `chore/`, `ci/`, `refactor/`, `test/`.
- One commit per PR is preferred. Squash-merge is the default.
- Subject line ≤ 72 chars, imperative mood. Body explains *why*, not *what* (the diff covers that).
- Conventional-commit examples: `feat(metrics): add per-pod tcp_sockets PromQL query`, `fix(orchestrator): handle missing nodeInfoAll gracefully`, `docs(README): document --seeds flag`.

## Gates (must pass before merge)

```bash
cd chaosprobe
uv run pytest -q              # all tests pass
uv run black --check .        # formatter clean
uv run ruff check .           # linter clean
```

GitHub Actions runs the same three checks on every PR. CI is the source of truth — if it fires red, fix on the branch and re-push; never merge red.

100% line and branch coverage on **new or changed executable code**. Existing untouched code, configuration, hand-authored docs, and type-only declarations are exempt. If a branch is genuinely unreachable, either assert it or add an explicit `# pragma: no cover` with a one-line reason.

## Typesafety bar

No `# type: ignore` and no `Any` returned from public APIs unless paired with a one-line comment naming the specific reason (boundary, third-party schema, etc.). If the type checker complains, fix the model — don't suppress the diagnostic.

## Testing conventions

- Unit tests live in `chaosprobe/tests/` and are pure-Python — they MUST NOT require a Kubernetes cluster, network, or any external service.
- Use `MagicMock` and the existing `tests/test_collector.py` helpers (`_make_node`, `_make_pod_status`, etc.) for K8s objects.
- Integration tests against a real cluster are out of scope for this repo. If you need to verify cluster behaviour, document the manual steps in the PR body.

## Where things live

- `chaosprobe/chaosprobe/metrics/` — probers (`prometheus`, `latency`, `redis`, `disk`, `resource`), statistics helpers, reproducibility metadata.
- `chaosprobe/chaosprobe/orchestrator/` — per-strategy + per-iteration orchestration, `aggregate_iterations`, `run_phases`.
- `chaosprobe/chaosprobe/output/` — output schema, comparison logic, charts.
- `chaosprobe/chaosprobe/commands/` — Click CLI subcommands (`run`, `stats`, `doctor`, `compare`, `visualize`, ...).
- `chaosprobe/chaosprobe/placement/` — placement strategy enum + mutator.
- `chaosprobe/scenarios/` — experiment YAMLs + scenario assets.
- `chaosprobe/tests/` — unit tests.
- `chaosprobe/docs/how-to/reproducing-thesis-results.md` — exact cluster / workload / fault matrix used in the thesis.
- `chaosprobe/TECHNICAL.md` — module reference + output schema.

## Adding a new per-strategy aggregate

Each per-strategy aggregate roll-up (e.g. `schedulerEventCounts`, `nodePressureEvents`) follows the same pattern:

1. Add the field(s) to `iter_result` in `chaosprobe/orchestrator/strategy_runner.py` if not already present.
2. Add the roll-up block in `aggregate_iterations` (`chaosprobe/orchestrator/run_phases.py`). Pattern:
   ```python
   if iteration_results carries the field:
       compute the totals / means / counts
       agg["yourNewField"] = ...
   ```
3. Tests: extend `tests/test_aggregate_iterations.py` or add `tests/test_<feature>_aggregation.py`. Cover: present-data case, absent-data case (block omitted), malformed-data case (silently skipped).
4. Document the field in `chaosprobe/TECHNICAL.md` Section 5's per-strategy aggregate table.

## Adding a new metric to `chaosprobe stats`

1. Extend `_METRIC_SPECS` in `chaosprobe/commands/stats_cmd.py` with `(dotted_path, label)`.
2. Add a test confirming the new key flows through CI + pairwise.
3. Document the new key in `TECHNICAL.md`'s Stats subsection.

## Reproducing the thesis numbers

See [`chaosprobe/docs/how-to/reproducing-thesis-results.md`](chaosprobe/docs/how-to/reproducing-thesis-results.md) — exact cluster spec, fault matrix, invocations, and the two falsifiable bars a reproducing run must clear.

## License

MIT.
