{"source":"pod-cpu-hog-helper-5qq2g","errorCode":"CONTAINER_RUNTIME_ERROR","phase":"ChaosInject","reason":"failed to get container pid: time=\"2025-05-31T19:21:37Z\" level=fatal msg=\"validate service connection: validate CRI v1 runtime API for endpoint \\\"unix:///run/containerd/containerd.sock\\\": rpc error: code = Unimplemented desc = unknown service runtime.v1.RuntimeService\"\n","target":"{containerID: 44b4401ad5a752d04f1d09f1f91a66003da1c92102b0b172d32fed5f03468fe4}"}

Replace in yaml
- name: SOCKET_PATH
value: /run/containerd/containerd.sock
with
- name: SOCKET_PATH
value: /run/k3s/containerd/containerd.sock