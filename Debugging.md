# Debugging

## Containerd Socket Path

If running on **k3s** (not the default — ChaosProbe uses Kubespray with standard containerd), chaos experiments may fail with:

```
CONTAINER_RUNTIME_ERROR: validate CRI v1 runtime API for endpoint "unix:///run/containerd/containerd.sock"
```

k3s uses a non-standard socket path. Fix by setting in the experiment YAML:

```yaml
- name: SOCKET_PATH
  value: /run/k3s/containerd/containerd.sock
```

This is **not needed** when using Kubespray or standard containerd clusters — the default path `/run/containerd/containerd.sock` is correct.