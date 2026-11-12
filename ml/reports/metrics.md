# Isolation Forest evaluation

Trained at **2026-11-12T13:20:15Z** (UTC) on synthetic pod metric windows.
Seed `42`, artifact version `1.0`.

## Overall (held-out test set)

| Metric | Value |
|--------|-------|
| Precision | 0.896 |
| Recall | 0.889 |
| F1 | 0.893 |
| Accuracy | 0.893 |

Confusion matrix: TN=350, FP=40,
FN=43, TP=346.

Train windows (normal only): 1170. Test windows:
390 normal + 389 anomalous.
Threshold: 0.0131 (90th percentile
of training scores).

## Per-behaviour recall

`normal` is specificity (share of healthy windows *not* flagged).

| Label | Recall |
|-------|--------|
| `normal` | 0.897 |
| `memory_leak` | 0.792 |
| `cpu_spike` | 0.886 |
| `restart_storm` | 1.000 |

## Notes

Trained on synthetic normal windows only. Threshold is the 90th percentile of training anomaly scores. recall_by_label['normal'] is specificity (1 - false-positive rate).
