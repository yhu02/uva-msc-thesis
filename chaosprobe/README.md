# ChaosProbe

A framework for running LitmusChaos experiments against Kubernetes deployments,
collecting structured experiment data into Neo4j, and turning it into
statistically-grounded comparisons of pod **placement strategies** under chaos —
with an AI feedback loop for anomaly classification and remediation.

## Install

```bash
cd chaosprobe
uv sync          # creates .venv, installs all dependencies
```

Optionally add a `.env` to override Neo4j / registry settings — see
[Configuration](docs/reference/configuration.md).

## 30-second taste (no cluster)

Every analysis command ships with a worked-example fixture, so you can see what
ChaosProbe produces without a cluster:

```bash
uv run chaosprobe recommend -s examples/example-summary.json
uv run chaosprobe report    -s examples/example-summary.json -o /tmp/report.md
```

Then follow the [Getting started tutorial](docs/tutorials/getting-started.md).

## Documentation

The docs follow the [Diátaxis](https://diataxis.fr/) framework — pick by what
you need. Full map: **[docs/index.md](docs/index.md)**.

| | |
|---|---|
| 🎓 **[Tutorial](docs/tutorials/getting-started.md)** | Learn by doing — your first analysis and first experiment. |
| 🔧 **[How-to guides](docs/index.md)** | [Set up a cluster](docs/how-to/set-up-a-cluster.md) · [Run experiments](docs/how-to/run-experiments.md) · [Analyze results](docs/how-to/analyze-results.md) · [Write a scenario](docs/how-to/write-a-scenario.md) · [Add a Rust probe](docs/how-to/add-a-rust-probe.md) · [Reproduce thesis results](docs/how-to/reproducing-thesis-results.md) |
| 📖 **[Reference](docs/reference/cli.md)** | [CLI](docs/reference/cli.md) · [Configuration](docs/reference/configuration.md) · [TECHNICAL.md](TECHNICAL.md) (modules, schemas) |
| 💡 **[Explanation](docs/explanation/concepts.md)** | [Concepts](docs/explanation/concepts.md) · [TECHNICAL.md](TECHNICAL.md) (methodology) |

[`TECHNICAL.md`](TECHNICAL.md) is the deep, citable technical write-up — the
consolidated reference + explanation appendix the Diátaxis tree links into.

## Development

```bash
uv sync                     # sync all dependencies (including dev)
uv run pytest               # run tests
uv run ruff check .         # lint
uv run black --check .      # check formatting
uv run mypy                 # type-check
```

See [`../CONTRIBUTING.md`](../CONTRIBUTING.md) for contribution guidelines.

## License

MIT
