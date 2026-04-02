# ChaosProbe Continuous Improvement Prompt

Copy-paste the prompt below into Claude Code. It will run 20 improvement cycles back-to-back, fixing one issue per cycle.

---

## Prompt

```
You are auditing and improving the ChaosProbe project at /home/yhu02/uva-msc-thesis/chaosprobe/.

ChaosProbe is a Kubernetes chaos testing framework (Python/Click CLI) that runs LitmusChaos experiments across pod placement strategies, collects metrics (recovery, latency, throughput, resources, Prometheus), stores everything in Neo4j + SQLite, and exports ML-ready datasets. It is an MSc thesis project.

## Your task

Run exactly 20 improvement cycles, one after another. For each cycle:

1. **Audit** — Pick the next category from the checklist below. Rotate through categories in order: Code quality -> Test coverage -> Data integrity -> Documentation accuracy -> Robustness -> CLI UX -> then repeat. Within each category, pick a different checklist item each time you visit that category. Thoroughly explore the relevant code.

2. **Identify** — Find the single highest-impact issue. Explain briefly what's wrong and why it matters.

3. **Fix** — Implement the fix. Keep changes minimal and focused — one issue per cycle.

4. **Verify** — Run `cd chaosprobe && uv run pytest -x -q` and `uv run ruff check .` (ignore pre-existing E501 line length warnings). If tests fail, fix before moving on.

5. **Log** — Print a short summary in this format:
   ```
   === Cycle N/20 ===
   Category: <category>
   Issue: <one-line description>
   Fix: <what you changed>
   Files: <list of modified files>
   Tests: <pass/fail count>
   ```

Then immediately proceed to cycle N+1. Do not stop or ask for confirmation between cycles.

After all 20 cycles, print a final summary table listing all 20 fixes.

## Audit checklist (rotate through these categories in order)

### 1. Code quality
- Unused imports, dead code, unreachable branches
- Functions that are too long (>80 lines) — extract helpers
- Duplicated logic across modules — extract shared utilities
- Inconsistent error handling (bare `except:`, swallowed exceptions)
- Missing type hints on public functions
- Magic numbers or hardcoded values that should be constants

### 2. Test coverage
- Modules with no test file (compare chaosprobe/chaosprobe/**/*.py vs tests/test_*.py)
- Public functions with zero test coverage
- Tests that don't assert anything meaningful (mock-only, no real logic tested)
- Edge cases: empty inputs, None values, malformed data
- Error paths: verify exceptions are raised correctly

### 3. Data integrity
- Neo4j sync_run: are all fields from output_data stored? Are any silently dropped?
- SQLite save_run: does the schema match current output format?
- ML export: are time-series properly aligned? Are labels correct?
- Comparison engine: does it handle missing/partial data gracefully?

### 4. Documentation accuracy
- README.md: do documented commands match actual CLI options?
- TECHNICAL.md: do module descriptions match implementations?
- Docstrings: do they match current function signatures and behavior?
- Inline comments that describe what the code used to do, not what it does now

### 5. Robustness
- Kubernetes API calls without proper timeout or retry
- File I/O without proper error handling
- Race conditions in concurrent probers (latency, throughput, resources, prometheus)
- Graceful degradation when optional services are unavailable (Prometheus, Neo4j, Redis)

### 6. CLI UX
- Confusing or missing error messages
- Missing progress indicators for long operations
- Default values that don't match documentation
- Help text that is vague or incorrect

## Key files

Source modules:
- cli.py (~3200 lines) — Main CLI entry point
- config/loader.py, config/validator.py — Scenario loading
- chaos/runner.py — ChaosEngine execution
- collector/result_collector.py — ChaosResult collection
- loadgen/runner.py — Locust load generation
- metrics/{recovery,collector,latency,throughput,resources,prometheus}.py — Metric collection
- metrics/{anomaly_labels,cascade,remediation,timeseries}.py — ML data processing
- output/{generator,comparison,visualize,ml_export}.py — Output pipeline
- placement/{strategy,mutator}.py — Pod placement
- provisioner/{kubernetes,setup}.py — Cluster provisioning
- storage/{base,sqlite,neo4j_store}.py — Data persistence
- graph/analysis.py — Graph analysis functions

Tests: tests/test_*.py (17 test files, ~313 tests)
Docs: README.md, TECHNICAL.md, scenarios/online-boutique/README.md

## Rules

- ONE fix per cycle. Don't try to fix everything at once.
- Don't add features. Only fix issues, remove dead code, improve correctness, or fix docs.
- Don't refactor working code just for style — only change things that are wrong, misleading, or fragile.
- Don't add comments, docstrings, or type hints to code you didn't otherwise change.
- Keep test changes focused: add missing tests or fix broken assertions, don't reorganize test files.
- After fixing, always run pytest and ruff to verify before moving to the next cycle.
- Do NOT stop between cycles. Run all 20 continuously.
- If you cannot find any issue in a category, log "No issue found" and move to the next cycle.
```
