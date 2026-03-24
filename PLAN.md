# ChaosProbe Improvement Plan

## Phase 1: Tighter Cluster-Experiment Coupling

**Goal:** Make worker node configuration (CPU, memory, disk, count) part of the scenario definition so experiments are reproducible.

**Tasks:**
1. Extend scenario YAML schema to include a `cluster:` section defining worker specs (count, cpu, memory, disk)
2. Update the validator to parse and validate cluster config
3. Modify `provisioner/setup.py` to read cluster config from scenario and pass it to Vagrant/k3s provisioning
4. Update `run-all` to optionally provision a fresh cluster per scenario

**Key files:** `chaosprobe/chaosprobe/cli.py`, `chaosprobe/chaosprobe/provisioner/setup.py`, `chaosprobe/chaosprobe/config/validator.py`

---

## Phase 2: Locust Integration for Live Metrics

**Goal:** Replace the passive Google loadgenerator with Locust for controllable load patterns and live metric collection.

**Tasks:**
1. Create a `chaosprobe/chaosprobe/loadgen/` module with a Locust wrapper
2. Write a default `locustfile.py` for the online-boutique frontend (configurable users, spawn rate, duration)
3. Collect Locust stats (response time p50/p95/p99, RPS, error rate) programmatically via Locust's `stats` module or CSV export
4. Add a `--load-profile` option to `run` and `run-all` (e.g., `steady`, `ramp`, `spike`)
5. Integrate Locust start/stop into the chaos pipeline — start before chaos, stop after, capture before/during/after windows

**Key files:** New module `loadgen/`, `chaosprobe/chaosprobe/cli.py`, scenario YAMLs

---

## Phase 3: Database Storage + Historical Tracking

**Goal:** Persist experiment results in a database for multi-run analysis and trend queries.

**Tasks:**
1. Create `chaosprobe/chaosprobe/storage/` module with an abstract store interface
2. Implement SQLite backend (zero-config, file-based, good for thesis scope)
3. Schema: `runs` (id, timestamp, scenario, strategy, cluster_config), `metrics` (run_id, metric_name, value, timestamp), `pod_placements` (run_id, pod, node, deployment)
4. Update `OutputGenerator` to write to DB in addition to JSON
5. Add `chaosprobe query` CLI command for basic queries (list runs, compare strategies, export CSV)

**Key files:** New module `storage/`, `chaosprobe/chaosprobe/output/generator.py`, `chaosprobe/chaosprobe/cli.py`

---

## Phase 4: Visualization + Correlation

**Goal:** Generate charts relating placement strategies to performance, and track pod-to-node mappings visually.

**Tasks:**
1. Create `chaosprobe/chaosprobe/output/visualize.py` using matplotlib/plotly
2. Charts: response latency per strategy (box plots), recovery time comparison, RPS over time with chaos injection markers
3. Pod-node heatmap: which pods on which nodes, colored by performance
4. Add `chaosprobe visualize` CLI command that reads from DB and outputs HTML/PNG
5. Integrate into `run-all` summary with `--visualize` flag

**Key files:** New `visualize.py`, `chaosprobe/chaosprobe/output/comparison.py`, `chaosprobe/chaosprobe/cli.py`

---

## Phase Order & Dependencies

```
Phase 1 (cluster coupling) ──┐
                              ├──> Phase 3 (database) ──> Phase 4 (visualization)
Phase 2 (Locust metrics)  ───┘
```

Phases 1 and 2 are independent and can be worked in parallel. Phase 3 needs the metric types from Phase 2 to design the schema well. Phase 4 needs Phase 3's query interface.

---

## Iterative Prompt

Use this prompt at the start of each work session:

> **Context:** I'm working on ChaosProbe, a Kubernetes chaos testing framework at `~/uva-msc-thesis/chaosprobe/`. The pipeline is: provision cluster → deploy app → apply placement strategy → run chaos → collect results. There are 4 improvement phases:
>
> 1. **Cluster-experiment coupling** — embed worker node specs in scenario YAML
> 2. **Locust integration** — replace passive loadgenerator with Locust for live latency/RPS metrics
> 3. **Database storage** — persist results in SQLite for historical analysis
> 4. **Visualization** — charts correlating placement strategies with performance
>
> **Current phase:** [Phase N]
> **What I did last:** [brief summary or "starting fresh"]
> **What I want to do now:** [specific task from the plan]
>
> Read the relevant source files, understand what exists, then implement the next incremental step. Keep changes small and testable. After each change, suggest what to test and what comes next.
