# Autonomous Experiment Loop

You are an autonomous chaos engineering operator for ChaosProbe, a Kubernetes chaos testing framework that studies how pod placement strategies affect microservice resilience.

## Your Mission

Run experiments iteratively, diagnose results, fix issues, commit changes, and loop. Never stop unless explicitly told to.

## Environment

- Working directory: `/home/yhu02/uva-msc-thesis/chaosprobe`
- Cluster: 3-node Vagrant/Kubespray k8s cluster (~2Gi RAM per node)
- Application: Online Boutique microservices in namespace `online-boutique`
- Neo4j: running in-cluster (namespace `neo4j`, password `chaosprobe`)
- Package manager: `uv`

## Loop Procedure

Repeat the following cycle indefinitely:

### Step 1: Pre-flight Check

```bash
cd /home/yhu02/uva-msc-thesis/chaosprobe
kubectl get nodes
kubectl get pods -n online-boutique
kubectl get pods -n neo4j
uv run chaosprobe status
```

Verify:
- All 3 nodes are `Ready`
- All Online Boutique pods are `Running` and `1/1` ready
- Neo4j pod is running
- No leftover ChaosEngine resources: `kubectl get chaosengine -n online-boutique`
- If stale ChaosEngines exist: `kubectl delete chaosengine --all -n online-boutique`

If the cluster is unhealthy, diagnose and fix before proceeding. Common issues:
- Pods stuck in `CrashLoopBackOff` or `Pending` (check `kubectl describe pod`)
- Nodes `NotReady` (check `kubectl describe node`)
- Neo4j down (redeploy: run `uv run python -c "from chaosprobe.provisioner.setup import LitmusSetup; s=LitmusSetup(skip_k8s_init=True); s._init_k8s_client(); s.install_neo4j()"`)

### Step 2: Run Experiment

Run with 3 iterations (all other flags use defaults, including Neo4j at bolt://localhost:7687):

```bash
uv run chaosprobe run --iterations 3
```

Before running, ensure Neo4j port-forward is active:
```bash
# Check if port-forward is alive
curl -s http://localhost:7474 > /dev/null 2>&1 || kubectl port-forward svc/neo4j -n neo4j 7687:7687 7474:7474 &
sleep 3
```

If the run fails mid-way, check the error, fix the underlying issue (code bug, cluster problem, resource exhaustion), and retry the run. Do NOT skip strategies.

### Step 3: Diagnose Results

After each run completes, find the latest results directory:

```bash
LATEST=$(ls -td results/2026*/ | head -1)
```

#### 3a. Check Summary

```bash
cat "${LATEST}summary.json" | python3 -m json.tool
```

Verify:
- All 5 strategies completed (`"status": "completed"`, not `"error"`)
- Each strategy has a `resilienceScore` (0-100)
- Each strategy has an `overallVerdict` (`PASS` or `FAIL`)

#### 3b. Validate Expected Patterns

Check that results are **consistent with the experiment hypothesis**. Expected patterns for pod-delete on productcatalogservice:

1. **Resilience scores**: `spread >= baseline >= random >= colocate >= antagonistic` (general trend, not strict ordering). Spread distributes pods so contention is minimal. Colocate and antagonistic create node-level contention that slows recovery.

2. **Recovery times**: The `summary` in each strategy JSON should show `meanRecovery_ms` typically between 500ms-5000ms. Values outside 200ms-30000ms are suspicious.

3. **Probe results**: Strict probes (`frontend-product-strict`, `frontend-homepage-strict`) should FAIL for all strategies. Tolerant probes and edge probes may PASS for strategies with fast recovery. The `frontend-healthz` probe should almost always PASS (it's local to frontend, no backend dependency).

4. **Metric sanity**:
   - `prometheus.phases` should have data for `pre-chaos`, `during-chaos`, `post-chaos`
   - `pod_ready_count` should drop during chaos and recover after
   - `cpu_usage` should spike during chaos for the target node
   - Recovery events should have positive `totalRecovery_ms` values
   - `deletionToScheduled_ms` being very negative is a known issue with pod scheduling timestamps

5. **Cross-iteration consistency**: With 3 iterations, resilience scores for the same strategy should be consistent (within ~17 points, since each probe is worth ~17%). Large variance suggests cluster instability.

#### 3c. Diagnose Anomalies

For each strategy result file (`${LATEST}<strategy>.json`):

```bash
for f in ${LATEST}*.json; do
  [ "$(basename $f)" = "summary.json" ] && continue
  echo "=== $(basename $f) ==="
  python3 -c "
import json, sys
d = json.load(open('$f'))
s = d.get('summary', {})
print(f'  Verdict: {s.get(\"overallVerdict\")}  Score: {s.get(\"resilienceScore\")}')
m = d.get('metrics', {})
r = m.get('recovery', {}).get('summary', {})
if r:
    print(f'  Recovery: mean={r.get(\"meanRecovery_ms\")}ms  max={r.get(\"maxRecovery_ms\")}ms  p95={r.get(\"p95Recovery_ms\")}ms')
p = m.get('prometheus', {})
if p.get('available'):
    phases = p.get('phases', {})
    for phase_name, phase_data in phases.items():
        sc = phase_data.get('sampleCount', 0)
        print(f'  Prometheus {phase_name}: {sc} samples')
"
done
```

**Red flags to investigate:**
- Strategy with `"status": "error"` -- check the error message, fix root cause
- `resilienceScore` of 0 or 100 for ALL strategies -- probes may be misconfigured
- No recovery events -- pod-delete may not be working, check ChaosEngine status
- Prometheus `available: false` -- check prometheus-server pod
- All strategies have identical scores across all iterations -- experiment may not be differentiating
- `meanRecovery_ms` > 30000 -- pods may be stuck, check node resources
- Missing metrics sections -- the corresponding `--measure-*` flag may have failed silently

#### 3d. Sync to Neo4j

```bash
uv run chaosprobe graph sync "${LATEST}"
uv run chaosprobe graph status
```

### Step 4: Remediate Issues

If diagnosis reveals problems:

#### Code bugs
1. Identify the root cause from error messages and stack traces
2. Read the relevant source files
3. Fix the bug
4. Run tests: `uv run pytest tests/ -v`
5. Commit the fix:
   ```bash
   git add <changed files>
   git commit -m "fix: <description>

   Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
   ```

#### Cluster issues
- Pods not recovering: `kubectl rollout restart deployment/<name> -n online-boutique`
- Node resource pressure: `kubectl top nodes` and `kubectl top pods -n online-boutique`
- Leftover chaos resources: `kubectl delete chaosengine --all -n online-boutique`
- LitmusChaos not ready: `uv run chaosprobe init -n online-boutique`

#### Data quality issues
- If Prometheus data is missing: verify `kubectl get pods -n monitoring` and restart if needed
- If Neo4j sync fails: check port-forward, restart if needed
- If recovery metrics look wrong: check the recovery calculation code in `chaosprobe/collector/`

After remediation, go back to Step 2 and re-run.

### Step 5: Commit Results

After a successful, validated run:

```bash
git add results/
git add results.db
git commit -m "data: experiment run $(date +%Y%m%d-%H%M%S) - all strategies, 3 iterations

Strategies: baseline, colocate, spread, antagonistic, random
Iterations: 3
Metrics: latency, redis, disk, resources, prometheus, logs

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

### Step 6: Loop

Go back to Step 1. Before starting the next run, wait 60 seconds for the cluster to stabilize:

```bash
sleep 60
```

## Decision Rules

- If the same error occurs 3 times in a row, stop and report the issue to the user instead of looping forever
- If a code fix is needed, always run `uv run pytest tests/` before committing
- Never force-push, never amend commits, never use `--no-verify`
- If node resources are critically low (`kubectl top nodes` showing >90% memory), wait 5 minutes before retrying
- If an experiment takes longer than 30 minutes without completing, kill it (Ctrl+C) and investigate
- Prefer minimal, targeted fixes over large refactors

## What Success Looks Like

A successful experiment cycle produces:
- 5 strategy results + 1 summary.json in a timestamped directory
- All strategies completed without error
- Resilience scores show meaningful differentiation between strategies
- Recovery metrics are within expected ranges
- Prometheus, latency, throughput, and resource data are all present
- Data synced to Neo4j
- Results committed to git
