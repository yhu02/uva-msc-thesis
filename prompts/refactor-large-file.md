# Task: Iterative Analysis and Refactoring of an Entire Codebase

## Context
This codebase has grown organically and needs a comprehensive architectural review and refactoring. You will analyze and refactor the **entire codebase** across **5 iterative cycles**. Each cycle builds on the previous one, progressively improving the architecture, code structure, and maintainability. Do NOT skip ahead — complete each cycle fully before moving to the next.

## Codebase to analyze
`/home/yhu02/uva-msc-thesis/chaosprobe/chaosprobe/` (Python package root)

## Project Context
**ChaosProbe** is a CLI tool for running LitmusChaos experiments on Kubernetes clusters and collecting resilience metrics. It studies how different pod placement strategies (colocate, spread, random, adversarial, best-fit, dependency-aware) affect application resilience under chaos injection.

### Tech Stack & Domain Constraints
- **CLI framework:** Click (entry point: `cli.py` → delegates to `commands/` submodules)
- **Kubernetes:** `kubernetes` Python client — reads/writes CRD YAMLs, manages pods, deployments, namespaces
- **Chaos engine:** LitmusChaos native ChaosEngine CRDs (YAML-based experiment definitions in `scenarios/`)
- **Metrics:** Prometheus queries via HTTP API (`metrics/prometheus.py`), plus custom metric collectors
- **Storage:** Neo4j graph database (`[graph]` extra)
- **Load generation:** Locust (`loadgen/`)
- **Visualization:** matplotlib (`output/charts.py`, `output/visualize.py`)
- **Build system:** uv + hatchling, Python 3.9+
- **Optional deps:** `neo4j` (graph extra), `pyarrow` (parquet extra) — must remain optional, not hard requirements

### Build & Test Commands
```bash
cd chaosprobe && uv sync          # install deps
uv run pytest                     # run tests
uv run black .                    # format
uv run ruff check .               # lint
uv run mypy chaosprobe            # type check
```

### Current Package Structure
```
chaosprobe/
├── __init__.py
├── cli.py                    (263 lines) — Click entry point, delegates to commands/
├── k8s.py                    — Kubernetes utility helpers
├── chaos/
│   └── runner.py             (609 lines) — Chaos experiment execution
├── collector/
│   └── result_collector.py   — Result collection
├── commands/                 — CLI command handlers (Click groups)
│   ├── shared.py             — Shared CLI utilities
│   ├── cluster_cmd.py        (576 lines) — Cluster management
│   ├── run_cmd.py            (519 lines) — Run orchestration CLI
│   ├── dashboard_cmd.py      — Dashboard command
│   ├── delete_cmd.py         — Delete command
│   ├── graph_cmd.py          — Graph command
│   ├── init_cmd.py           — Init command
│   ├── placement_cmd.py      — Placement command
│   ├── probe_cmd.py          — Probe command
│   └── visualize_cmd.py      — Visualize command
├── config/
│   ├── loader.py             — YAML config loading
│   ├── topology.py           — Topology configuration
│   └── validator.py          (399 lines) — Config validation
├── graph/
│   └── analysis.py           — Graph-based analysis
├── loadgen/
│   └── runner.py             — Locust load generation
├── metrics/                  — Metric collection & analysis
│   ├── base.py               — Base metric classes
│   ├── collector.py          (353 lines) — Metric collector orchestration
│   ├── prometheus.py         (485 lines) — Prometheus HTTP API client
│   ├── latency.py            (717 lines) — Latency metrics
│   ├── throughput.py         (674 lines) — Throughput metrics
│   ├── resources.py          (365 lines) — Resource usage metrics
│   ├── recovery.py           — Recovery time metrics
│   ├── remediation.py        — Remediation metrics
│   ├── cascade.py            — Cascade failure metrics
│   ├── anomaly_labels.py     — Anomaly labeling
│   └── timeseries.py         — Time series handling
├── orchestrator/             — Run orchestration
│   ├── portforward.py        — K8s port forwarding
│   ├── preflight.py          — Pre-run validation
│   ├── probers.py            — Probe execution
│   ├── run_phases.py         (696 lines) — Run phase management
│   └── strategy_runner.py    (433 lines) — Strategy execution
├── output/                   — Output generation
│   ├── charts.py             (870 lines) — Chart generation
│   ├── visualize.py          (484 lines) — Visualization
│   ├── comparison.py         (372 lines) — Cross-strategy comparison
│   ├── generator.py          — Output file generation
│   └── ml_export.py          — ML-ready data export
├── placement/                — Pod placement strategies
│   ├── strategy.py           — Strategy definitions
│   └── mutator.py            (506 lines) — Placement mutation
├── probes/                   — Chaos probe definitions
│   ├── builder.py            (346 lines) — Probe builder
│   └── templates.py          — Probe templates
├── provisioner/              — Cluster provisioning
│   ├── chaoscenter.py        (1118 lines) — LitmusChaos center integration
│   ├── setup.py              (1008 lines) — Cluster setup orchestration
│   ├── components.py         (433 lines) — Component installation
│   ├── kubernetes.py         — K8s provisioning helpers
│   └── vagrant.py            (612 lines) — Vagrant VM management
└── storage/                  — Data persistence
    ├── neo4j_store.py        — Neo4j store interface
    ├── neo4j_reader.py       (876 lines) — Neo4j read operations
    └── neo4j_writer.py       (872 lines) — Neo4j write operations
```

### Known Oversized Files (>300 lines)
Thirteen source files exceed 300 lines, with `provisioner/chaoscenter.py` (1118) and `provisioner/setup.py` (1008) being the largest. The `metrics/`, `output/`, and `storage/` packages each have multiple large files that likely contain mixed concerns.

## Loop Instruction
**Repeat this entire prompt (all 5 cycles below) 5 times.** After completing Cycle 5, go back to Cycle 1 and run through all cycles again, building on the improvements from the previous pass. Each full pass should find and fix issues that prior passes missed or introduced. Continue until you have completed **5 full passes** (25 total cycles).

---

## Cycle 1: Codebase-Wide Analysis & Discovery

Explore the entire codebase and produce a detailed report covering:

1. **Project structure** — map out all packages, modules, and their relationships
2. **Responsibility mapping** — for each file/module, summarize its purpose and identify files that handle multiple unrelated concerns
3. **Dependency graph** — map inter-module dependencies, identify tightly coupled components and circular imports
4. **Oversized files** — list files that are too large or contain too many responsibilities (flag anything over ~300 lines or with more than one clear concern)
5. **Code smells** — god classes/modules, duplicated logic across files, inconsistent patterns, mixed abstraction levels, dead code
6. **Metrics** — lines per file, number of functions/classes per file, max function length, overall package structure depth
7. **Inconsistencies** — naming conventions, error handling patterns, logging approaches, configuration access patterns that vary across the codebase

**Deliverable:** A written analysis document covering the full codebase. Do not change any code yet.

**Checkpoint:** Present findings and wait for confirmation before proceeding.

---

## Cycle 2: Architecture Redesign

Based on the Cycle 1 analysis, propose a new codebase architecture.

1. **High-level package structure** — define the top-level packages and their responsibilities
2. **Module breakdown** — for each package, define the modules within it:
   - **Name**: descriptive module name
   - **Responsibility**: single-sentence purpose (Single Responsibility Principle)
   - **Contents**: list of functions/classes that belong here
   - **Dependencies**: what it imports from other modules/packages
   - **Public API**: what it exports
3. **Migration map** — for each existing file, show where its contents move to in the new structure

Present the proposed structure as a tree with one-line responsibility descriptions per module. Example format:
```
chaosprobe/
├── cli.py                — Thin CLI entry point
├── config/
│   ├── loader.py         — YAML config loading
│   └── validator.py      — Config validation rules
├── chaos/
│   ├── engine.py         — Experiment lifecycle
│   └── probes.py         — Probe construction
└── metrics/
    ├── collector.py      — Metric orchestration
    └── prometheus.py     — Prometheus API client
```

Also identify:
- Which migrations are independent vs. which must move together
- Circular dependencies and how to break them
- Migration order (easiest/most independent first)
- Which existing APIs must be preserved for backward compatibility

**Deliverable:** Full architecture proposal + prioritized migration plan. No code changes yet.

**Checkpoint:** Present the proposed architecture and wait for confirmation before proceeding.

---

## Cycle 3: Foundation & Core Extractions

Execute the **foundational** portion of the migration plan from Cycle 2:

1. **Shared types & models** — extract shared data structures, types, constants, and configuration into their own modules
2. **Utility consolidation** — deduplicate shared helpers across files, place domain-specific helpers with their domain
3. **Independent modules** — extract modules with no or minimal inbound dependencies
4. For each extraction:
   - Create the new module/package
   - Move the relevant code
   - Update all imports across the entire codebase
   - Ensure backward compatibility where needed
5. Run tests / verify nothing breaks after each extraction

**Deliverable:** Working codebase with foundational modules extracted and all tests passing.

**Checkpoint:** Present what was extracted, confirm tests pass, and wait for confirmation before proceeding.

---

## Cycle 4: Deep Refactoring & Remaining Extractions

Execute the **remaining** migrations, tackling the more complex, tightly-coupled parts:

1. **Split oversized files** — break apart files that still handle multiple concerns
2. **Decouple tightly-bound modules** — use dependency injection, interfaces, or shared types modules to break circular dependencies
3. **Restructure packages** — move modules into their correct packages per the Cycle 2 design
4. **Slim down entry points** — CLI/main files should be thin orchestration layers that delegate to domain modules
5. Run tests / verify nothing breaks after each change

**Deliverable:** Working codebase matching the target architecture, all tests passing.

**Checkpoint:** Present the final structure, confirm tests pass, and wait for confirmation before proceeding.

---

## Cycle 5: Polish, Validate & Document

Final quality pass over the entire refactored codebase:

1. **Review each module** — does it have a single clear responsibility? Is anything misplaced?
2. **Clean up imports** — remove unused imports, sort and organize across all files
3. **Consistency pass** — ensure naming conventions, error handling, and patterns are consistent codebase-wide
4. **Dead code removal** — remove any orphaned code, unused re-exports, or compatibility shims that are no longer needed
5. **Check public APIs** — ensure all external entry points and interfaces still work
6. **Verify test coverage** — run full test suite, flag any gaps introduced by the refactor
7. **Document** — add module-level docstrings to each new/modified file explaining its purpose

**Deliverable:** Final refactored codebase, all tests passing, with a summary of all changes made across the 5 cycles including:
- Before/after directory tree comparison
- List of files created, moved, split, or deleted
- Any API changes or breaking changes introduced

---

## Constraints (apply to all cycles)
- Prefer composition over inheritance
- Each module should be testable in isolation
- Avoid creating modules with only 1-2 small functions unless they represent a clear distinct concern
- Don't create a `utils.py` dumping ground — if helpers are domain-specific, they belong with their domain
- Minimize circular dependencies; if unavoidable, use dependency injection or a shared types module
- Preserve existing public APIs where possible — the `chaosprobe` CLI entry point must remain `chaosprobe.cli:main`
- The codebase must remain functional after every cycle — no big-bang rewrites
- Run tests (`uv run pytest`) after every significant change, not just at the end of a cycle
- Run linting/formatting (`uv run ruff check .` and `uv run black --check .`) before committing
- Commit after each cycle so changes can be reviewed and reverted independently
- Keep `neo4j` and `pyarrow` as optional dependencies — guard imports with try/except or feature checks
- Kubernetes CRD YAML files in `scenarios/` define experiment schemas — do not change their format
- Click command group structure (cli → subcommands) should remain, but commands should be thin wrappers delegating to domain logic
- After completing all 5 cycles, **loop back to Cycle 1** and repeat. Do this until you have completed **5 full passes** through all cycles. Each pass should catch issues the previous pass missed or introduced. Label each pass clearly (Pass 1/5, Pass 2/5, etc.).
