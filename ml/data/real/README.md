# Real cluster metrics (data card)

Labelled per-pod Prometheus samples from a real single-node **kind** cluster
while Chaos Mesh experiments ran on a schedule. Collected with
`make collect` (`ml/collect.py`); consumed by `make train-real`
(`ml/train_real.py`). Each run directory holds the full export, not a
sample: it is a few hundred KB gzipped.

| File | What it is |
|------|------------|
| `<run>/points.csv.gz` | one row per (pod, 15s timestamp) |
| `<run>/timeline.json` | the scripted phases (baseline / chaos / recovery) with unix start/end and whether Chaos Mesh reported the injection |
| `<run>/summary.json` | row/pod counts per label and the run's time range |

## Columns (`points.csv.gz`)

| Column | Meaning |
|--------|---------|
| `ts`, `time_utc` | sample time (Prometheus range-query step, 15s) |
| `namespace`, `pod` | pod identity; every namespace in the cluster is included |
| `cpu` | cores, `rate(container_cpu_usage_seconds_total[2m])` summed over containers |
| `memory_bytes` | `container_memory_working_set_bytes` summed over containers |
| `memory_limit_bytes` | `kube_pod_container_resource_limits{resource="memory"}`, 0 if unset |
| `restarts` | cumulative `kube_pod_container_status_restarts_total` |
| `net_rx_bytes_per_s`, `net_tx_bytes_per_s` | `rate(container_network_*_bytes_total[2m])` |
| `phase` | timeline phase at that time (`baseline`, `chaos:<scenario>`, `recovery:<scenario>`, `outside`) |
| `label` | `normal`, a scenario name, `recovery`, or `injection_failed` (see below) |

The PromQL is `ml/promql.py`, the same queries the live API uses for scoring.

## Labels

A sample gets a scenario label only if its pod belongs to that scenario's
target deployment (pod name prefix, e.g. `victim-cpu-`) **and** the sample
falls inside the chaos window. The first 60s after a window are `recovery`.
Every other pod (system pods, `steady`, other victims) is `normal`, even
during chaos. Training windows (12 samples = 3 min) take the majority
scenario label. Windows that are only partly chaos or recovery are dropped
(`ml/real_data.py`).

| Scenario | How it is injected | Target |
|----------|--------------------|--------|
| `cpu_stress` | Chaos Mesh `StressChaos`, 1 worker at 80% load | `victim-cpu` (2 nginx pods) |
| `memory_stress` | `StressChaos`, 1 vm worker holding 96MB (128Mi limit) | `victim-mem` |
| `memory_leak` | a busybox pod appending 1 MiB to `/dev/shm` every 3s, scaled 0 -> 2 for the window | `victim-leak` |
| `restart_storm` | `Schedule` of `PodChaos` container-kill every 30s | `victim-restart` |
| `pod_kill` | `Schedule` of `PodChaos` pod-kill every 60s | `victim-kill` |
| `network_delay` | `NetworkChaos` 200ms +/- 50ms | `victim-net`, **not collected on the box** (see below) |

A `loadgen` pod curls every service once a second, so there is steady
background traffic.

## Runs

| Run | Wall clock | Rows | Pods | Timeline |
|-----|-----------|------|------|----------|
| `run-box-01` | 67.4 min (14:20 to 15:27 UTC, 19:50 to 20:57 IST, 12 Nov 2026) | 7,106 | 43 | 15 min baseline, then 5 scenarios x (6 min chaos + 3 min recovery) |
| `run-box-02` | 45.0 min (15:28 to 16:13 UTC, 20:58 to 21:43 IST, 12 Nov 2026) | 5,118 | 41 | 10 min baseline, then 5 scenarios x (5 min chaos + 2 min recovery) |

`run-box-01` is the training run. `run-box-02` was collected after the
training and evaluation code was frozen and is used only as a held-out test
set.

## Known limitations (read before quoting numbers)

- **Small and single-node.** One kind node, nginx/busybox victims, about two
  hours in total. Chaos windows give only about 36 to 100 windows per
  scenario, and overlapping windows (stride 1) are correlated.
- **Sampling gaps.** The collection box was paused for a few minutes at a time
  when idle, so `run-box-01` has a few 75s to 225s holes in every series (its
  15-minute baseline really covers about 17 minutes of wall clock with gaps). `ml/real_data.py` splits series at
  gaps, so no window spans a hole.
- **`network_delay` was not collected.** The box's kernel has no `sch_netem`
  module, so Chaos Mesh could not apply `tc` rules there. The experiment
  manifest works on a normal Linux laptop with kind, so run `make collect`
  without `--skip network_delay` to include it.
- **Box-specific cluster tweaks** that a normal laptop doesn't need: kube-proxy
  was switched to `nftables` mode because the kernel lacks iptables
  `statistic` matching, and images were side-loaded with `kind load` because
  DNS inside the kind node could not reach the registries.
- `pod_kill` replaces pods every minute, so each replacement lives about 4
  samples. A per-pod 3-minute window can't see a deletion, which shows up as
  0 recall for `pod_kill`. It needs deployment-level or event features.
- The very first sample of a run can show `phase=outside` because Prometheus
  timestamps are whole seconds. Its label is still correct (`normal`).

## Reproduce

```bash
make cluster-up metrics-up chaos-up
make collect-dry-run COLLECT_ARGS="--skip network_delay"   # print the plan
make collect                                                # ~60 min, writes ml/data/real/run-*/
make train-real                                             # reports + MLflow
```
