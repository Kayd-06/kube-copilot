# Isolation Forest on real cluster metrics

Data: Prometheus metrics from a single-node kind cluster while Chaos Mesh
experiments ran on a schedule (see `ml/data/real/README.md`).

- `run-box-01`: 67.4 min wall clock, 7106 pod samples, 43 pods (2026-11-12T14:20Z to 2026-11-12T15:27Z)
- `run-box-02`: 45.0 min wall clock, 5118 pod samples, 41 pods (2026-11-12T15:28Z to 2026-11-12T16:13Z)

## Method

- Window = 12 samples x 15s = 3 minutes of one pod, the same features as the live API.
- Every model is trained on **normal windows only** and thresholded at the
  90th percentile of its own training scores, so test data never sets a threshold.
- No leakage: training windows always end before any test window starts.
  Ambiguous windows (chaos edges, a minute of recovery after chaos) are dropped.
- Windows overlap (stride 1), so neighbouring test windows are correlated.
  These are estimates from about two hours of one laptop-sized cluster, not a benchmark.

Models:

- `real`: Isolation Forest on real normal windows (the method defined before any real data existed).
- `real+guard`: the same forest plus an out-of-range guard. It flags a window when
  any model feature lies more than one training-range width outside the training
  [p0.5, p99.5] band, because Isolation Forest cannot score points far beyond its
  training range above the most extreme training point. **The guard was designed
  after looking at the in-run results below**, so its in-run numbers are optimistic.
  The held-out run was collected and scored only after the code was frozen; treat
  that section as the honest estimate.
- `synthetic`: the existing synthetic-trained model (`make train` defaults).
- `synthetic+guard`, `synthetic+real+guard`: synthetic-only, and synthetic plus real normal windows, with the guard.

Scopes (applied to training and test windows alike):

- **all namespaces**: every pod in the cluster.
- **app namespaces only**: drops `chaos-mesh`, `kube-system`, `observability` and
  `local-path-storage`. Those are cluster plumbing rather than workloads a user
  asks the copilot about, and their behaviour is driven by the experiments
  themselves (the chaos daemon works hardest while injecting) or grows all run
  (Prometheus' TSDB). This scope was also chosen after the first in-run results;
  it was fixed before the held-out run was scored.

## In-run time split (`run-box-01`), all namespaces

- Split: time split inside one run at 2026-11-12T14:37:19Z.
- Train windows: 1435 real normal
  (+ 1170 synthetic normal for the `synthetic*` models).
- Test windows: `{"cpu_stress": 38, "memory_leak": 36, "memory_stress": 50, "normal": 4189, "pod_kill": 102, "restart_storm": 50}`.

### Overall

| Metric | `real` | `real+guard` | `synthetic` | `synthetic+guard` | `synthetic+real+guard` |
|---|---|---|---|---|---|
| Precision | 0.113 | 0.185 | 0.061 | 0.061 | 0.240 |
| Recall | 0.330 | 0.594 | 0.986 | 0.986 | 0.496 |
| F1 | 0.168 | 0.282 | 0.115 | 0.115 | 0.324 |
| Specificity | 0.829 | 0.828 | 0.003 | 0.003 | 0.897 |

- `real`: TN=3474, FP=715, FN=185, TP=91
- `real+guard`: TN=3467, FP=722, FN=112, TP=164
- `synthetic`: TN=12, FP=4177, FN=4, TP=272
- `synthetic+guard`: TN=12, FP=4177, FN=4, TP=272
- `synthetic+real+guard`: TN=3756, FP=433, FN=139, TP=137

### Per scenario

Each scenario's precision / recall / F1 is computed on that scenario's windows
plus every normal test window.

| Scenario | Windows | `real` P / R / F1 | `real+guard` P / R / F1 | `synthetic` P / R / F1 | `synthetic+guard` P / R / F1 | `synthetic+real+guard` P / R / F1 |
|---|---|---|---|---|---|---|
| `cpu_stress` | 38 | 0.003 / 0.053 / 0.005 | 0.050 / 1.000 / 0.095 | 0.009 / 1.000 / 0.018 | 0.009 / 1.000 / 0.018 | 0.081 / 1.000 / 0.149 |
| `memory_leak` | 36 | 0.035 / 0.722 / 0.067 | 0.035 / 0.722 / 0.066 | 0.008 / 0.889 / 0.015 | 0.008 / 0.889 / 0.015 | 0.052 / 0.667 / 0.097 |
| `memory_stress` | 50 | 0.030 / 0.440 / 0.056 | 0.065 / 1.000 / 0.122 | 0.012 / 1.000 / 0.023 | 0.012 / 1.000 / 0.023 | 0.055 / 0.500 / 0.098 |
| `pod_kill` | 102 | 0.000 / 0.000 / 0.000 | 0.000 / 0.000 / 0.000 | 0.024 / 1.000 / 0.047 | 0.024 / 1.000 / 0.047 | 0.000 / 0.000 / 0.000 |
| `restart_storm` | 50 | 0.054 / 0.820 / 0.102 | 0.065 / 1.000 / 0.122 | 0.012 / 1.000 / 0.023 | 0.012 / 1.000 / 0.023 | 0.103 / 1.000 / 0.188 |

### False-positive rate by namespace (normal test windows)

| Namespace | `real` | `real+guard` | `synthetic` | `synthetic+guard` | `synthetic+real+guard` |
|---|---|---|---|---|---|
| `chaos-lab` | 0.030 | 0.034 | 0.997 | 0.997 | 0.014 |
| `chaos-mesh` | 0.492 | 0.492 | 1.000 | 1.000 | 0.275 |
| `kube-system` | 0.250 | 0.250 | 1.000 | 1.000 | 0.196 |
| `local-path-storage` | 0.000 | 0.000 | 1.000 | 1.000 | 0.000 |
| `observability` | 0.333 | 0.333 | 0.986 | 0.986 | 0.113 |

### Threshold sensitivity (`real`, Isolation Forest only)

For transparency only; the tables above use the pre-declared
90th percentile of training scores.

| Percentile | Threshold | Precision | Recall | F1 | Specificity |
|---|---|---|---|---|---|
| p90 | 0.0329 | 0.113 | 0.330 | 0.168 | 0.829 |
| p95 | 0.0505 | 0.131 | 0.221 | 0.164 | 0.903 |
| p99 | 0.0812 | 0.206 | 0.138 | 0.165 | 0.965 |

## Held-out run (train `run-box-01`, test `run-box-02`), all namespaces

- Split: train on every normal window of the training run, test on a later held-out run.
- Train windows: 5624 real normal
  (+ 1170 synthetic normal for the `synthetic*` models).
- Test windows: `{"cpu_stress": 42, "memory_leak": 30, "memory_stress": 42, "normal": 4335, "pod_kill": 78, "restart_storm": 42}`.

### Overall

| Metric | `real` | `real+guard` | `synthetic` | `synthetic+guard` | `synthetic+real+guard` |
|---|---|---|---|---|---|
| Precision | 0.162 | 0.227 | 0.051 | 0.051 | 0.385 |
| Recall | 0.410 | 0.632 | 1.000 | 1.000 | 0.538 |
| F1 | 0.232 | 0.334 | 0.098 | 0.098 | 0.449 |
| Specificity | 0.885 | 0.884 | 0.001 | 0.001 | 0.954 |

- `real`: TN=3838, FP=497, FN=138, TP=96
- `real+guard`: TN=3831, FP=504, FN=86, TP=148
- `synthetic`: TN=5, FP=4330, FN=0, TP=234
- `synthetic+guard`: TN=5, FP=4330, FN=0, TP=234
- `synthetic+real+guard`: TN=4134, FP=201, FN=108, TP=126

### Per scenario

Each scenario's precision / recall / F1 is computed on that scenario's windows
plus every normal test window.

| Scenario | Windows | `real` P / R / F1 | `real+guard` P / R / F1 | `synthetic` P / R / F1 | `synthetic+guard` P / R / F1 | `synthetic+real+guard` P / R / F1 |
|---|---|---|---|---|---|---|
| `cpu_stress` | 42 | 0.044 / 0.548 / 0.082 | 0.077 / 1.000 / 0.143 | 0.010 / 1.000 / 0.019 | 0.010 / 1.000 / 0.019 | 0.173 / 1.000 / 0.295 |
| `memory_leak` | 30 | 0.044 / 0.767 / 0.084 | 0.044 / 0.767 / 0.083 | 0.007 / 1.000 / 0.014 | 0.007 / 1.000 / 0.014 | 0.095 / 0.700 / 0.167 |
| `memory_stress` | 42 | 0.031 / 0.381 / 0.058 | 0.075 / 0.976 / 0.140 | 0.010 / 1.000 / 0.019 | 0.010 / 1.000 / 0.019 | 0.103 / 0.548 / 0.173 |
| `pod_kill` | 78 | 0.000 / 0.000 / 0.000 | 0.000 / 0.000 / 0.000 | 0.018 / 1.000 / 0.035 | 0.018 / 1.000 / 0.035 | 0.000 / 0.000 / 0.000 |
| `restart_storm` | 42 | 0.064 / 0.809 / 0.119 | 0.077 / 1.000 / 0.143 | 0.010 / 1.000 / 0.019 | 0.010 / 1.000 / 0.019 | 0.166 / 0.952 / 0.283 |

### False-positive rate by namespace (normal test windows)

| Namespace | `real` | `real+guard` | `synthetic` | `synthetic+guard` | `synthetic+real+guard` |
|---|---|---|---|---|---|
| `chaos-lab` | 0.031 | 0.034 | 1.000 | 1.000 | 0.006 |
| `chaos-mesh` | 0.221 | 0.221 | 1.000 | 1.000 | 0.015 |
| `kube-system` | 0.173 | 0.173 | 0.999 | 0.999 | 0.127 |
| `local-path-storage` | 0.000 | 0.000 | 1.000 | 1.000 | 0.000 |
| `observability` | 0.247 | 0.247 | 0.994 | 0.994 | 0.024 |

### Threshold sensitivity (`real`, Isolation Forest only)

For transparency only; the tables above use the pre-declared
90th percentile of training scores.

| Percentile | Threshold | Precision | Recall | F1 | Specificity |
|---|---|---|---|---|---|
| p90 | 0.0223 | 0.162 | 0.410 | 0.232 | 0.885 |
| p95 | 0.0433 | 0.243 | 0.355 | 0.289 | 0.941 |
| p99 | 0.0788 | 0.302 | 0.162 | 0.211 | 0.980 |

## In-run time split (`run-box-01`), app namespaces only

- Split: time split inside one run at 2026-11-12T14:37:19Z.
- Train windows: 690 real normal
  (+ 1170 synthetic normal for the `synthetic*` models).
- Test windows: `{"cpu_stress": 38, "memory_leak": 36, "memory_stress": 50, "normal": 1875, "pod_kill": 102, "restart_storm": 50}`.

### Overall

| Metric | `real` | `real+guard` | `synthetic` | `synthetic+guard` | `synthetic+real+guard` |
|---|---|---|---|---|---|
| Precision | 0.495 | 0.505 | 0.127 | 0.127 | 0.733 |
| Recall | 0.721 | 0.750 | 0.986 | 0.986 | 0.536 |
| F1 | 0.587 | 0.604 | 0.225 | 0.225 | 0.619 |
| Specificity | 0.892 | 0.892 | 0.003 | 0.003 | 0.971 |

- `real`: TN=1672, FP=203, FN=77, TP=199
- `real+guard`: TN=1672, FP=203, FN=69, TP=207
- `synthetic`: TN=5, FP=1870, FN=4, TP=272
- `synthetic+guard`: TN=5, FP=1870, FN=4, TP=272
- `synthetic+real+guard`: TN=1821, FP=54, FN=128, TP=148

### Per scenario

Each scenario's precision / recall / F1 is computed on that scenario's windows
plus every normal test window.

| Scenario | Windows | `real` P / R / F1 | `real+guard` P / R / F1 | `synthetic` P / R / F1 | `synthetic+guard` P / R / F1 | `synthetic+real+guard` P / R / F1 |
|---|---|---|---|---|---|---|
| `cpu_stress` | 38 | 0.154 / 0.974 / 0.266 | 0.158 / 1.000 / 0.272 | 0.020 / 1.000 / 0.039 | 0.020 / 1.000 / 0.039 | 0.413 / 1.000 / 0.585 |
| `memory_leak` | 36 | 0.151 / 1.000 / 0.262 | 0.151 / 1.000 / 0.262 | 0.017 / 0.889 / 0.033 | 0.017 / 0.889 / 0.033 | 0.317 / 0.694 / 0.435 |
| `memory_stress` | 50 | 0.175 / 0.860 / 0.290 | 0.198 / 1.000 / 0.330 | 0.026 / 1.000 / 0.051 | 0.026 / 1.000 / 0.051 | 0.325 / 0.520 / 0.400 |
| `pod_kill` | 102 | 0.140 / 0.324 / 0.195 | 0.140 / 0.324 / 0.195 | 0.052 / 1.000 / 0.098 | 0.052 / 1.000 / 0.098 | 0.143 / 0.088 / 0.109 |
| `restart_storm` | 50 | 0.198 / 1.000 / 0.330 | 0.198 / 1.000 / 0.330 | 0.026 / 1.000 / 0.051 | 0.026 / 1.000 / 0.051 | 0.481 / 1.000 / 0.649 |

### False-positive rate by namespace (normal test windows)

| Namespace | `real` | `real+guard` | `synthetic` | `synthetic+guard` | `synthetic+real+guard` |
|---|---|---|---|---|---|
| `chaos-lab` | 0.108 | 0.108 | 0.997 | 0.997 | 0.029 |

### Threshold sensitivity (`real`, Isolation Forest only)

For transparency only; the tables above use the pre-declared
90th percentile of training scores.

| Percentile | Threshold | Precision | Recall | F1 | Specificity |
|---|---|---|---|---|---|
| p90 | -0.0221 | 0.495 | 0.721 | 0.587 | 0.892 |
| p95 | 0.0502 | 0.437 | 0.442 | 0.440 | 0.916 |
| p99 | 0.1138 | 0.611 | 0.199 | 0.300 | 0.981 |

## Held-out run (train `run-box-01`, test `run-box-02`), app namespaces only

- Split: train on every normal window of the training run, test on a later held-out run.
- Train windows: 2565 real normal
  (+ 1170 synthetic normal for the `synthetic*` models).
- Test windows: `{"cpu_stress": 42, "memory_leak": 30, "memory_stress": 42, "normal": 1955, "pod_kill": 78, "restart_storm": 42}`.

### Overall

| Metric | `real` | `real+guard` | `synthetic` | `synthetic+guard` | `synthetic+real+guard` |
|---|---|---|---|---|---|
| Precision | 0.478 | 0.480 | 0.107 | 0.107 | 0.847 |
| Recall | 0.782 | 0.791 | 1.000 | 1.000 | 0.590 |
| F1 | 0.593 | 0.598 | 0.193 | 0.193 | 0.695 |
| Specificity | 0.898 | 0.898 | 0.000 | 0.000 | 0.987 |

- `real`: TN=1755, FP=200, FN=51, TP=183
- `real+guard`: TN=1755, FP=200, FN=49, TP=185
- `synthetic`: TN=0, FP=1955, FN=0, TP=234
- `synthetic+guard`: TN=0, FP=1955, FN=0, TP=234
- `synthetic+real+guard`: TN=1930, FP=25, FN=96, TP=138

### Per scenario

Each scenario's precision / recall / F1 is computed on that scenario's windows
plus every normal test window.

| Scenario | Windows | `real` P / R / F1 | `real+guard` P / R / F1 | `synthetic` P / R / F1 | `synthetic+guard` P / R / F1 | `synthetic+real+guard` P / R / F1 |
|---|---|---|---|---|---|---|
| `cpu_stress` | 42 | 0.174 / 1.000 / 0.296 | 0.174 / 1.000 / 0.296 | 0.021 / 1.000 / 0.041 | 0.021 / 1.000 / 0.041 | 0.627 / 1.000 / 0.771 |
| `memory_leak` | 30 | 0.130 / 1.000 / 0.231 | 0.130 / 1.000 / 0.231 | 0.015 / 1.000 / 0.030 | 0.015 / 1.000 / 0.030 | 0.510 / 0.867 / 0.642 |
| `memory_stress` | 42 | 0.167 / 0.952 / 0.284 | 0.174 / 1.000 / 0.296 | 0.021 / 1.000 / 0.041 | 0.021 / 1.000 / 0.041 | 0.500 / 0.595 / 0.543 |
| `pod_kill` | 78 | 0.127 / 0.372 / 0.189 | 0.127 / 0.372 / 0.189 | 0.038 / 1.000 / 0.074 | 0.038 / 1.000 / 0.074 | 0.107 / 0.038 / 0.057 |
| `restart_storm` | 42 | 0.174 / 1.000 / 0.296 | 0.174 / 1.000 / 0.296 | 0.021 / 1.000 / 0.041 | 0.021 / 1.000 / 0.041 | 0.627 / 1.000 / 0.771 |

### False-positive rate by namespace (normal test windows)

| Namespace | `real` | `real+guard` | `synthetic` | `synthetic+guard` | `synthetic+real+guard` |
|---|---|---|---|---|---|
| `chaos-lab` | 0.102 | 0.102 | 1.000 | 1.000 | 0.013 |

### Threshold sensitivity (`real`, Isolation Forest only)

For transparency only; the tables above use the pre-declared
90th percentile of training scores.

| Percentile | Threshold | Precision | Recall | F1 | Specificity |
|---|---|---|---|---|---|
| p90 | -0.0199 | 0.478 | 0.782 | 0.593 | 0.898 |
| p95 | 0.0646 | 0.490 | 0.521 | 0.505 | 0.935 |
| p99 | 0.1091 | 0.819 | 0.444 | 0.576 | 0.988 |
