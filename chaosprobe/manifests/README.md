# In-cluster registry for ChaosProbe probe images

ChaosProbe's Rust `cmdProbe` images have to live in a registry the cluster can
`docker pull` from. Instead of depending on an external registry (GHCR auth +
package visibility), `chaosprobe init` installs a small private `registry:2` on
the control-plane node and `chaosprobe run` pushes/pulls probe images there
automatically.

`run` **pushes through a `kubectl port-forward` tunnel** to the registry
Service, so the build host needs only kubectl access — no route to the
registry's NodePort and no docker `insecure-registries` config (the push goes
to `127.0.0.1`, which docker trusts as insecure by default; no `docker login`
either). This works the same on Docker Desktop, native Linux, or a remote build
host.

The **one manual step** is trusting the registry on each node's containerd, so
the kubelet can *pull* the images over plain HTTP (step 2) — that's node-level
config outside the Kubernetes API.

> ⚠️ Always target the thesis kubeconfig. The machine's *default* `KUBECONFIG`
> may point at an unrelated cluster. In every shell:
> ```bash
> export KUBECONFIG=~/.kube/config-chaosprobe
> kubectl config current-context   # confirm it's the thesis cluster
> ```

## 1. Install the registry

```bash
export KUBECONFIG=~/.kube/config-chaosprobe
chaosprobe init                 # installs + wires the registry (use --skip-registry to opt out)
```
`init` prints the registry address, e.g. `192.168.56.11:30500` (control-plane
node IP + NodePort). To install/inspect it without full init:
`kubectl apply -f manifests/registry.yaml`. Sanity check (should print `{}`):
```bash
curl http://<registry-address>/v2/
```

## 2. Trust it on every node (containerd) — so the kubelet can pull over HTTP

containerd verifies TLS by default, so each node needs the registry marked
insecure. containerd must have `config_path = "/etc/containerd/certs.d"`
(Kubespray sets this). Then on each node (`chaosprobe cluster vagrant ssh <node>`):
```bash
sudo mkdir -p /etc/containerd/certs.d/<registry-address>
sudo tee /etc/containerd/certs.d/<registry-address>/hosts.toml >/dev/null <<EOF
server = "http://<registry-address>"
[host."http://<registry-address>"]
  capabilities = ["pull", "resolve"]
  skip_verify = true
EOF
sudo systemctl restart containerd
```
Reproducible alternative (Kubespray) — put this in inventory `group_vars` and
re-run the containerd role so it survives rebuilds:
```yaml
containerd_insecure_registries:
  "<registry-address>": "http://<registry-address>"
```

## 3. Run

```bash
export KUBECONFIG=~/.kube/config-chaosprobe
uv run chaosprobe run -i 1 --strategies baseline,default,colocate,spread
```
`run` resolves the registry's address automatically, opens a `kubectl
port-forward` to the registry Service, and pushes the probe images through it
(tagging them with the node-reachable `<registry-address>` that cmdProbe pods
pull from). If the registry isn't installed, `run` fails with a message telling
you to run `chaosprobe init`. Verify the push landed:
```bash
curl http://<registry-address>/v2/_catalog
```

## Troubleshooting

- **`run` fails with "In-cluster registry not found"** → the registry isn't installed/ready; run `chaosprobe init` and check `kubectl -n registry get pods`.
- **`run` fails with "registry port-forward did not become ready / failed"** → kubectl can't reach the cluster or the registry Service is missing. Check `kubectl config current-context` (thesis cluster) and `kubectl -n registry get svc registry`.
- **Pods `ImagePullBackOff` with an HTTP/HTTPS error** → step 2 not done on the node that scheduled the probe pod (or its containerd wasn't restarted).
