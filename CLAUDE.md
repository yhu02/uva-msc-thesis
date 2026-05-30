# CLAUDE.md — autonomous cluster setup + experiment runs

Guidance for Claude Code to provision the thesis cluster with Vagrant and run
ChaosProbe experiments end to end. Full docs: [`chaosprobe/docs/index.md`](chaosprobe/docs/index.md).

This flow is **mostly** autonomous, but a few steps require the user — they are
flagged ⚠️ below. Honor those gates; do not try to work around them.

---

## ⛔ Safety gate — verify the target cluster before ANY chaos (non-negotiable)

`chaosprobe` acts on whatever `KUBECONFIG` points at, and it does **not** check
which cluster that is. Another kubeconfig on this machine targets a
**corporate / production AKS cluster** (`aie-*` namespaces). Running chaos there
would be catastrophic.

**Before** `init`, `run`, `provision`, `placement`, `delete`, `cleanup`, or any
chaos command, confirm **both**:

```bash
echo "$KUBECONFIG"                 # MUST be ~/.kube/config-chaosprobe
kubectl config current-context     # MUST be the Vagrant thesis cluster (never aks / aie-*)
```

If either is wrong, missing, unexpected, or you are unsure → **STOP and ask the
user.** Never run a cluster-mutating or chaos command against a cluster you did
not just provision and verify in this session.

---

## What you own vs. what the user owns

You drive the `chaosprobe` CLI. The **user** owns privileged host/node
operations — do not attempt them; stop and hand over (suggest they run it via
`! <command>` in the prompt so the output lands in the session):

- ⚠️ `cluster vagrant setup` / `deploy` when they need **sudo or libvirt** (VM
  provider setup, ansible "become" password).
- ⚠️ The one-time **in-cluster registry trust** step on each node's containerd
  and the build host — node-level config outside the K8s API. See
  [`chaosprobe/manifests/README.md`](chaosprobe/manifests/README.md).
- ⚠️ Any node restart or containerd reload.

---

## Setup (Vagrant) — run from `chaosprobe/`

```bash
cd chaosprobe && uv sync
```

Then, pausing at every ⚠️ gate:

1. `uv run chaosprobe cluster vagrant init --control-planes 1 --workers 4`
2. ⚠️ `uv run chaosprobe cluster vagrant setup` — checks libvirt/KVM. If it
   reports anything `MISSING` / `NOT RUNNING`, or a sudo password is required,
   hand this to the user.
3. `uv run chaosprobe cluster vagrant up`
4. ⚠️ `uv run chaosprobe cluster vagrant deploy` — installs Kubernetes; **15–30
   min and may need sudo**. Long-running: run it in the background and poll;
   don't block the turn. If it prompts for sudo, hand to the user.
5. `uv run chaosprobe cluster vagrant kubeconfig` then
   `export KUBECONFIG=~/.kube/config-chaosprobe`
6. **Run the safety gate above**, then `uv run chaosprobe status` to confirm
   prerequisites + cluster connectivity before going further.

(Already have a cluster? Skip 1–4, just export the thesis kubeconfig and run the
safety gate. Other cluster paths — Kubespray, Proxmox — are in
[`chaosprobe/docs/how-to/set-up-a-cluster.md`](chaosprobe/docs/how-to/set-up-a-cluster.md).)

---

## Initialize + run experiments

7. `uv run chaosprobe init` — installs LitmusChaos, ChaosCenter, Prometheus,
   Neo4j, metrics-server, and the in-cluster registry.
   - ⚠️ If probe-image **pulls fail**, the registry node-trust step (user-owned,
     above) hasn't been done — hand it to the user, or re-init with
     `--skip-registry` and point `CHAOSPROBE_REGISTRY` at an external registry.
8. Run the experiment matrix. **This is long** (per iteration ≈ 60 s settle +
   120 s chaos + 60 s post, × each strategy × iterations — hours for the full
   matrix). Run in the background and poll.

   ```bash
   # Full multi-fault matrix (recommended for results — varies fault class
   # while holding placement/target/probes constant):
   uv run chaosprobe run -n online-boutique \
       -e scenarios/online-boutique/placement-experiment.yaml \
       -e scenarios/online-boutique/placement-experiment-cpuhog.yaml \
       -i 5

   # Fast smoke check first (one strategy pass, one iteration):
   uv run chaosprobe run -n online-boutique -i 1
   ```

---

## Analyze + report

9. `uv run chaosprobe doctor -s <run-output>/summary.json --strict` — gate data
   quality before trusting numbers.
10. `uv run chaosprobe recommend -s <run-output>/summary.json` and
    `uv run chaosprobe report -s <run-output>/summary.json -o report.md`.
    Surface the recommendation and the report path to the user.

Full analysis command set:
[`chaosprobe/docs/how-to/analyze-results.md`](chaosprobe/docs/how-to/analyze-results.md).

---

## Teardown

- `uv run chaosprobe cluster vagrant halt` — stops the VMs, **preserves** state
  (restart later with `up`). This is the default when done.
- ⚠️ `uv run chaosprobe cluster vagrant destroy` — **destructive** (deletes the
  VMs). Only on explicit user request.

---

## Conventions

- Long ops (`deploy`, `run`): launch in the background and poll; don't block.
- Do **not** commit run outputs, `summary.json`, charts, or reports unless asked.
- Stop at the ⚠️ gates rather than guessing sudo passwords or node config.
- Reference: [`chaosprobe/docs/index.md`](chaosprobe/docs/index.md) (Diátaxis map),
  [`chaosprobe/TECHNICAL.md`](chaosprobe/TECHNICAL.md) (deep reference).
