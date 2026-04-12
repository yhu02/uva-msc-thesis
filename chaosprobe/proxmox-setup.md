# Proxmox Server Integration Guide

How to run ChaosProbe experiments on an external Proxmox server.

## Overview

ChaosProbe runs locally on your machine and interacts with the cluster via `kubectl` and port-forwards. You create VMs on Proxmox, ChaosProbe deploys Kubernetes on them via Kubespray (Ansible), then runs experiments remotely.

## Prerequisites

### Proxmox VMs

Create at least 1 VM (combined control plane + worker) or ideally 3+ VMs on your Proxmox server.

| Role | CPU | RAM | Disk | OS |
|------|-----|-----|------|----|
| Control plane only | 2 cores | 2 GB | 20 GB | Ubuntu 22.04 Server |
| Worker | 2-4 cores | 4-8 GB | 20-40 GB | Ubuntu 22.04 Server |
| Combined (control plane + worker) | 2-4 cores | 6-8 GB | 20-40 GB | Ubuntu 22.04 Server |

All infrastructure (metrics-server, Prometheus, ChaosCenter, Neo4j) is pinned to the control plane to isolate it from chaos experiments on workers. Workers only run application workloads. In a single-VM setup (`roles: [control_plane, worker]`), everything shares one node — at least 6 GB is needed to fit K8s components, the infrastructure stack, and application workloads together.

Ensure SSH is running on each VM.

### Local machine

```bash
sudo apt install git python3-venv openssh-client
```

Also required: `kubectl`, `helm`, [uv](https://docs.astral.sh/uv/), Python 3.9+.

## Step 1: Setup SSH access

```bash
# Generate key if you don't have one
ssh-keygen -t ed25519

# Copy to each VM
ssh-copy-id ubuntu@<VM1_IP>
ssh-copy-id ubuntu@<VM2_IP>
ssh-copy-id ubuntu@<VM3_IP>

# Verify passwordless login works
ssh ubuntu@<VM1_IP> "hostname"
```

Ensure the user has passwordless sudo:

```bash
# On each VM
echo "ubuntu ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/ubuntu
```

## Step 2: Create hosts file

```yaml
# hosts.yaml
hosts:
  - name: master1
    ip: <VM1_IP>
    ansible_user: ubuntu
    roles: [control_plane, worker]
  - name: worker1
    ip: <VM2_IP>
    ansible_user: ubuntu
    roles: [worker]
  - name: worker2
    ip: <VM3_IP>
    ansible_user: ubuntu
    roles: [worker]
```

For a single-VM setup, use `roles: [control_plane, worker]` on that one host.

## Step 3: Deploy Kubernetes

```bash
cd chaosprobe

# Clones Kubespray, generates Ansible inventory, deploys K8s (15-30 min)
uv run chaosprobe cluster create --hosts-file hosts.yaml
```

This SSH-es into each VM and installs Kubernetes via Kubespray v2.24.0.

## Step 4: Fetch kubeconfig

```bash
uv run chaosprobe cluster kubeconfig --host <VM1_IP> --user ubuntu
export KUBECONFIG=~/.kube/config-chaosprobe

# Verify
kubectl cluster-info
kubectl get nodes
```

Add the export to your shell profile to persist it:

```bash
echo 'export KUBECONFIG=~/.kube/config-chaosprobe' >> ~/.bashrc
```

## Step 5: Install infrastructure

```bash
# Installs LitmusChaos, ChaosCenter, Prometheus, Neo4j, metrics-server
# Sets up kubectl port-forwards automatically
uv run chaosprobe init
```

This installs everything needed on the cluster and establishes port-forwards from your local machine:

| Service | Local Port | Purpose |
|---------|-----------|---------|
| Prometheus | 9090 | Cluster metrics queries |
| Neo4j (bolt) | 7687 | Graph database storage |
| Neo4j (HTTP) | 7474 | Neo4j browser |
| ChaosCenter | 9091-9093 | Experiment submission + dashboard |

## Step 6: Run experiments

```bash
uv run chaosprobe run -n online-boutique
```

This deploys the application, runs chaos experiments across all placement strategies, and stores results in Neo4j.

## How it works

```
Local machine                          Proxmox VMs
┌──────────────┐     kubectl           ┌─────────────────────┐
│ ChaosProbe   │ ──────────────────▶   │ Kubernetes cluster  │
│              │     port-forward      │  ├── LitmusChaos    │
│ kubectl      │ ◀────────────────────│  ├── ChaosCenter    │
│ port-forward │     localhost:9090    │  ├── Prometheus     │
│              │     localhost:7687    │  ├── Neo4j          │
│ Locust       │     localhost:8089    │  └── App (Online    │
│ (load gen)   │ ──────────────────▶   │      Boutique)      │
└──────────────┘                       └─────────────────────┘
```

- ChaosProbe **never runs inside** the cluster
- All interaction goes through `kubectl` (via `KUBECONFIG`) and `kubectl port-forward`
- SSH is only used during initial cluster setup (steps 3-4)

## Teardown

```bash
# Remove ChaosProbe infrastructure but keep cluster
uv run chaosprobe delete

# Destroy the entire cluster
uv run chaosprobe cluster destroy --inventory ~/.chaosprobe/kubespray/inventory
```

## Troubleshooting

### Port-forwards die

ChaosProbe auto-restarts port-forwards, but if they fail persistently:

```bash
# Re-establish manually
uv run chaosprobe init --skip-litmus
```

### Kubespray fails during deployment

- Verify SSH access: `ssh ubuntu@<IP> "sudo whoami"` should print `root`
- Check VM has internet access for package downloads
- Ensure no firewall blocks ports 6443 (K8s API), 2379-2380 (etcd), 10250 (kubelet)

### kubectl can't reach cluster

```bash
# Verify kubeconfig points to correct IP
grep server ~/.kube/config-chaosprobe

# Test connectivity
curl -k https://<VM1_IP>:6443/healthz
```

### Nodes not ready

```bash
# Check node status
kubectl get nodes -o wide

# Check kubelet on the VM
ssh ubuntu@<VM_IP> "sudo systemctl status kubelet"
```
