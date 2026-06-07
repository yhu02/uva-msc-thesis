# ChaosProbe analysis report

Source: `results/20260607-123004/summary.json`

## Data quality (doctor)

### node-memory-hog__baseline

- _warn_ — no recovery times collected — recovery-based stats unavailable

### node-memory-hog__colocate

- _warn_ — no recovery times collected — recovery-based stats unavailable

### node-memory-hog__default

- _warn_ — no recovery times collected — recovery-based stats unavailable

### node-memory-hog__spread

- _warn_ — no recovery times collected — recovery-based stats unavailable

### pod-delete__baseline

- _warn_ — no recovery times collected — recovery-based stats unavailable

## Per-strategy aggregate (summarize)

### node-memory-hog__baseline

```
iterations: 4
resilience: mean=100.0, stddev=0.0, p25=100.0, harmonic=100.0
  95% CI: [100.0, 100.0] (n=4)
load failures (top by occurrences):
  ConnectionRefusedError(111, 'Connection  /                    total=163 iters=4
  ConnectionRefusedError(111, 'Connection  /product/OLJCESPC7Z  total=64 iters=4
  ConnectionRefusedError(111, 'Connection  /cart/checkout       total=23 iters=4
  ConnectionRefusedError(111, 'Connection  /cart                total=18 iters=4
scheduler events:
  Killing              total=168 mean/iter=42 max/iter=48 (in 4 iter)
  Pulled               total=168 mean/iter=42 max/iter=48 (in 4 iter)
  Scheduled            total=165 mean/iter=41.25 max/iter=46 (in 4 iter)
experimentDuration: mean=309.1s, stddev=183.2s
```

### node-memory-hog__colocate

```
iterations: 4
resilience: mean=95.8, stddev=8.5, p25=95.8, harmonic=95.1
  95% CI: [87.2, 100.0] (n=4)
load failures (top by occurrences):
  ConnectionRefusedError(111, 'Connection  /                    total=65 iters=4
  ConnectionRefusedError(111, 'Connection  /product/OLJCESPC7Z  total=18 iters=2
  ConnectionRefusedError(111, 'Connection  /cart/checkout       total=9 iters=4
  ConnectionRefusedError(111, 'Connection  /cart                total=5 iters=4
scheduler events:
  Killing              total=38 mean/iter=9.5 max/iter=11 (in 4 iter)
  Pulled               total=38 mean/iter=9.5 max/iter=11 (in 4 iter)
  Scheduled            total=38 mean/iter=9.5 max/iter=11 (in 4 iter)
experimentDuration: mean=339.3s, stddev=0.4s
```

### node-memory-hog__default

```
iterations: 4
resilience: mean=100.0, stddev=0.0, p25=100.0, harmonic=100.0
  95% CI: [100.0, 100.0] (n=4)
load failures (top by occurrences):
  ConnectionRefusedError(111, 'Connection  /                    total=70 iters=4
  ConnectionRefusedError(111, 'Connection  /product/OLJCESPC7Z  total=19 iters=4
  ConnectionRefusedError(111, 'Connection  /cart/checkout       total=10 iters=4
  ConnectionRefusedError(111, 'Connection  /cart                total=4 iters=4
scheduler events:
  Killing              total=64 mean/iter=16 max/iter=26 (in 4 iter)
  Pulled               total=64 mean/iter=16 max/iter=26 (in 4 iter)
  Scheduled            total=59 mean/iter=14.75 max/iter=26 (in 4 iter)
experimentDuration: mean=336.9s, stddev=1.3s
```

### node-memory-hog__spread

```
iterations: 4
resilience: mean=100.0, stddev=0.0, p25=100.0, harmonic=100.0
  95% CI: [100.0, 100.0] (n=4)
load failures (top by occurrences):
  ConnectionRefusedError(111, 'Connection  /                    total=127 iters=4
  ConnectionRefusedError(111, 'Connection  /product/OLJCESPC7Z  total=45 iters=4
  ConnectionRefusedError(111, 'Connection  /cart/checkout       total=20 iters=4
  ConnectionRefusedError(111, 'Connection  /cart                total=10 iters=4
scheduler events:
  Killing              total=36 mean/iter=9 max/iter=9 (in 4 iter)
  Pulled               total=36 mean/iter=9 max/iter=9 (in 4 iter)
  Scheduled            total=36 mean/iter=9 max/iter=9 (in 4 iter)
experimentDuration: mean=337.8s, stddev=1.2s
```

### pod-delete__baseline

```
iterations: 4
resilience: mean=100.0, stddev=0.0, p25=100.0, harmonic=100.0
  95% CI: [100.0, 100.0] (n=4)
load failures (top by occurrences):
  ConnectionRefusedError(111, 'Connection  /                    total=99 iters=4
  ConnectionRefusedError(111, 'Connection  /product/OLJCESPC7Z  total=33 iters=4
  ConnectionRefusedError(111, 'Connection  /cart/checkout       total=14 iters=4
  ConnectionRefusedError(111, 'Connection  /cart                total=9 iters=4
scheduler events:
  Killing              total=123 mean/iter=30.75 max/iter=33 (in 4 iter)
  Pulled               total=123 mean/iter=30.75 max/iter=33 (in 4 iter)
  Scheduled            total=123 mean/iter=30.75 max/iter=33 (in 4 iter)
experimentDuration: mean=232.9s, stddev=29.7s
```

### pod-delete__colocate

```
iterations: 4
resilience: mean=70.5, stddev=25.0, p25=70.5, harmonic=60.4
  95% CI: [45.5, 83.0] (n=4)
recovery: mean=2187.4ms, stddev=445.8ms, median=1996.0ms, max=7392.0ms, p95=3340.9ms
  CV: 0.204
  95% CI: [1934.5, 2631.6] (n=4)
d2s (deletion→scheduled): mean=667.0ms, CV=0.086, CI=[619.6, 714.5] (n=4)
s2r (scheduled→ready):     mean=1520.0ms, CV=0.266, CI=[1310.5, 1924.0] (n=4)
recovery histogram (per iteration):
  lt_500ms             0  
  500_to_1000ms        0  
  1000_to_2000ms       2  ████████████████████
  2000_to_5000ms       2  ████████████████████
  5000_to_10000ms      0  
  gte_10000ms          0  
load: rps=15.19, errorRate=0.0519, responseTime=582.5ms
load failures (top by occurrences):
  HTTPError('500 Server Error: Internal Se /                    total=553 iters=4
  HTTPError('500 Server Error: Internal Se /product/OLJCESPC7Z  total=287 iters=4
  HTTPError('500 Server Error: Internal Se /cart                total=274 iters=4
  ConnectionRefusedError(111, 'Connection  /                    total=72 iters=3
  HTTPError('500 Server Error: Internal Se /cart/checkout       total=35 iters=4
scheduler events:
  Killing              total=219 mean/iter=54.75 max/iter=59 (in 4 iter)
  Pulled               total=221 mean/iter=55.25 max/iter=59 (in 4 iter)
  Scheduled            total=219 mean/iter=54.75 max/iter=59 (in 4 iter)
experimentDuration: mean=340.9s, stddev=5.7s
```

### pod-delete__default

```
iterations: 4
resilience: mean=70.5, stddev=25.0, p25=70.5, harmonic=60.4
  95% CI: [45.5, 83.0] (n=4)
recovery: mean=1509.9ms, stddev=304.7ms, median=1406.1ms, max=3209.0ms, p95=2105.2ms
  CV: 0.202
  95% CI: [1310.2, 1799.5] (n=4)
d2s (deletion→scheduled): mean=480.8ms, CV=0.141, CI=[424.8, 536.8] (n=4)
s2r (scheduled→ready):     mean=1028.6ms, CV=0.328, CI=[817.6, 1356.0] (n=4)
recovery histogram (per iteration):
  lt_500ms             0  
  500_to_1000ms        0  
  1000_to_2000ms       4  ████████████████████
  2000_to_5000ms       0  
  5000_to_10000ms      0  
  gte_10000ms          0  
load: rps=15.46, errorRate=0.0207, responseTime=220.0ms
load failures (top by occurrences):
  HTTPError('500 Server Error: Internal Se /                    total=230 iters=4
  HTTPError('500 Server Error: Internal Se /product/OLJCESPC7Z  total=113 iters=4
  HTTPError('500 Server Error: Internal Se /cart                total=90 iters=4
  ConnectionRefusedError(111, 'Connection  /                    total=68 iters=4
  ConnectionRefusedError(111, 'Connection  /product/OLJCESPC7Z  total=17 iters=3
scheduler events:
  Killing              total=160 mean/iter=40 max/iter=44 (in 4 iter)
  Pulled               total=160 mean/iter=40 max/iter=44 (in 4 iter)
  Scheduled            total=161 mean/iter=40.25 max/iter=44 (in 4 iter)
experimentDuration: mean=336.6s, stddev=1.8s
```

### pod-delete__spread

```
iterations: 4
resilience: mean=70.5, stddev=25.0, p25=70.5, harmonic=60.4
  95% CI: [45.5, 83.0] (n=4)
recovery: mean=1674.4ms, stddev=285.2ms, median=1594.2ms, max=2488.0ms, p95=2102.7ms
  CV: 0.170
  95% CI: [1465.3, 1923.2] (n=4)
d2s (deletion→scheduled): mean=625.3ms, CV=0.152, CI=[529.1, 681.5] (n=4)
s2r (scheduled→ready):     mean=1048.5ms, CV=0.227, CI=[875.7, 1274.4] (n=4)
recovery histogram (per iteration):
  lt_500ms             0  
  500_to_1000ms        0  
  1000_to_2000ms       3  ████████████████████
  2000_to_5000ms       1  ██████
  5000_to_10000ms      0  
  gte_10000ms          0  
load: rps=14.92, errorRate=0.0160, responseTime=722.5ms
load failures (top by occurrences):
  HTTPError('500 Server Error: Internal Se /                    total=136 iters=4
  HTTPError('500 Server Error: Internal Se /product/OLJCESPC7Z  total=74 iters=4
  ConnectionRefusedError(111, 'Connection  /                    total=66 iters=4
  HTTPError('500 Server Error: Internal Se /cart                total=36 iters=4
  HTTPError('500 Server Error: Internal Se /cart/checkout       total=23 iters=4
scheduler events:
  Killing              total=196 mean/iter=49 max/iter=57 (in 4 iter)
  Pulled               total=196 mean/iter=49 max/iter=57 (in 4 iter)
  Scheduled            total=196 mean/iter=49 max/iter=57 (in 4 iter)
experimentDuration: mean=340.0s, stddev=1.5s
```

## Statistical analysis (stats)

### resilienceScore

**Bootstrap 95% CI (mean):**

| strategy | n | mean | CI low | CI high |
|---|---:|---:|---:|---:|
| node-memory-hog__baseline | 4 | 100.0 | 100.0 | 100.0 |
| node-memory-hog__colocate | 4 | 95.75 | 87.25 | 100.0 |
| node-memory-hog__default | 4 | 100.0 | 100.0 | 100.0 |
| node-memory-hog__spread | 4 | 100.0 | 100.0 | 100.0 |
| pod-delete__baseline | 4 | 100.0 | 100.0 | 100.0 |
| pod-delete__colocate | 4 | 70.5 | 45.5 | 83.0 |
| pod-delete__default | 4 | 70.5 | 45.5 | 83.0 |
| pod-delete__spread | 4 | 70.5 | 45.5 | 83.0 |

**Pairwise Mann-Whitney U (Holm-Bonferroni adjusted):**

| a | b | mean_a | mean_b | p_raw | p_holm | Cliff's δ | magnitude | sig (α=.05) |
|---|---|---:|---:|---:|---:|---:|---|---:|
| pod-delete__baseline | pod-delete__default | 100.0 | 70.5 | 0.0177 | 0.4956 | 1.0 | large |  |
| pod-delete__baseline | pod-delete__spread | 100.0 | 70.5 | 0.0177 | 0.4956 | 1.0 | large |  |
| pod-delete__baseline | pod-delete__colocate | 100.0 | 70.5 | 0.0177 | 0.4956 | 1.0 | large |  |
| pod-delete__default | node-memory-hog__baseline | 70.5 | 100.0 | 0.0177 | 0.4956 | -1.0 | large |  |
| pod-delete__default | node-memory-hog__default | 70.5 | 100.0 | 0.0177 | 0.4956 | -1.0 | large |  |
| pod-delete__default | node-memory-hog__spread | 70.5 | 100.0 | 0.0177 | 0.4956 | -1.0 | large |  |
| pod-delete__spread | node-memory-hog__baseline | 70.5 | 100.0 | 0.0177 | 0.4956 | -1.0 | large |  |
| pod-delete__spread | node-memory-hog__default | 70.5 | 100.0 | 0.0177 | 0.4956 | -1.0 | large |  |
| pod-delete__spread | node-memory-hog__spread | 70.5 | 100.0 | 0.0177 | 0.4956 | -1.0 | large |  |
| pod-delete__colocate | node-memory-hog__baseline | 70.5 | 100.0 | 0.0177 | 0.4956 | -1.0 | large |  |
| pod-delete__colocate | node-memory-hog__default | 70.5 | 100.0 | 0.0177 | 0.4956 | -1.0 | large |  |
| pod-delete__colocate | node-memory-hog__spread | 70.5 | 100.0 | 0.0177 | 0.4956 | -1.0 | large |  |
| pod-delete__default | node-memory-hog__colocate | 70.5 | 95.75 | 0.0578 | 0.9248 | -0.8125 | large |  |
| pod-delete__spread | node-memory-hog__colocate | 70.5 | 95.75 | 0.0578 | 0.9248 | -0.8125 | large |  |
| pod-delete__colocate | node-memory-hog__colocate | 70.5 | 95.75 | 0.0578 | 0.9248 | -0.8125 | large |  |
| pod-delete__baseline | node-memory-hog__baseline | 100.0 | 100.0 | 1.0 | 1.0 | 0.0 | negligible |  |
| pod-delete__baseline | node-memory-hog__default | 100.0 | 100.0 | 1.0 | 1.0 | 0.0 | negligible |  |
| pod-delete__baseline | node-memory-hog__spread | 100.0 | 100.0 | 1.0 | 1.0 | 0.0 | negligible |  |
| pod-delete__baseline | node-memory-hog__colocate | 100.0 | 95.75 | 0.4533 | 1.0 | 0.25 | small |  |
| pod-delete__default | pod-delete__spread | 70.5 | 70.5 | 1.0 | 1.0 | 0.0 | negligible |  |
| pod-delete__default | pod-delete__colocate | 70.5 | 70.5 | 1.0 | 1.0 | 0.0 | negligible |  |
| pod-delete__spread | pod-delete__colocate | 70.5 | 70.5 | 1.0 | 1.0 | 0.0 | negligible |  |
| node-memory-hog__baseline | node-memory-hog__default | 100.0 | 100.0 | 1.0 | 1.0 | 0.0 | negligible |  |
| node-memory-hog__baseline | node-memory-hog__spread | 100.0 | 100.0 | 1.0 | 1.0 | 0.0 | negligible |  |
| node-memory-hog__baseline | node-memory-hog__colocate | 100.0 | 95.75 | 0.4533 | 1.0 | 0.25 | small |  |
| node-memory-hog__default | node-memory-hog__spread | 100.0 | 100.0 | 1.0 | 1.0 | 0.0 | negligible |  |
| node-memory-hog__default | node-memory-hog__colocate | 100.0 | 95.75 | 0.4533 | 1.0 | 0.25 | small |  |
| node-memory-hog__spread | node-memory-hog__colocate | 100.0 | 95.75 | 0.4533 | 1.0 | 0.25 | small |  |
