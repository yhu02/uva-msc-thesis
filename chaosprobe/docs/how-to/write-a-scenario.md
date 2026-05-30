# How to write a scenario

A **scenario** is a *directory* of standard Kubernetes manifests plus
ChaosEngine YAML. ChaosProbe auto-classifies files by their `kind` field — no
manifest list to maintain.

```
scenarios/nginx-pod-delete/
  deployment.yaml     # standard K8s Deployment
  service.yaml        # standard K8s Service
  experiment.yaml     # ChaosEngine experiment
  probes/             # (optional) custom Rust probes — see add-a-rust-probe.md
```

Worked, realistic examples live in
[`../../scenarios/online-boutique/`](../../scenarios/online-boutique/) — start by
copying one.

## The ChaosEngine experiment

The experiment file is a LitmusChaos `ChaosEngine`. The fault is the entry under
`spec.experiments[].name` (e.g. `pod-delete`, `pod-cpu-hog`, `pod-network-loss`
— see [the supported list](../explanation/concepts.md#supported-chaos-experiments)).

```yaml
apiVersion: litmuschaos.io/v1alpha1
kind: ChaosEngine
metadata:
  name: nginx-pod-delete
spec:
  engineState: active
  appinfo:
    appns: chaosprobe-test
    applabel: app=nginx
    appkind: deployment
  chaosServiceAccount: litmus-admin
  experiments:
    - name: pod-delete
      spec:
        components:
          env:
            - name: TOTAL_CHAOS_DURATION
              value: "30"
            - name: CHAOS_INTERVAL
              value: "10"
        probe:
          - name: http-probe
            type: httpProbe
            mode: Continuous
            httpProbe/inputs:
              url: http://nginx-service.chaosprobe-test.svc.cluster.local
              method:
                get:
                  criteria: "=="
                  responseCode: "200"
            runProperties:
              probeTimeout: 5s
              interval: 2s
              retry: 3
```

## Validating

`chaosprobe run` validates manifests and the ChaosEngine before executing. To
deploy the manifests without running chaos (useful while iterating on a
scenario):

```bash
uv run chaosprobe provision scenarios/nginx-pod-delete
```

## Next

- [Add a Rust probe](add-a-rust-probe.md) — custom `cmdProbe` checks.
- [Run experiments](run-experiments.md) — execute the scenario.
- The Online Boutique scenario set:
  [`../../scenarios/online-boutique/README.md`](../../scenarios/online-boutique/README.md).
