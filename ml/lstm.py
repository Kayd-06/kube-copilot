"""Optional LSTM autoencoder for pod metric windows.

This is *not* the default Phase 3 model. Isolation Forest is smaller, trains
on CPU in a few seconds, and is what `make train` and the API serve. The LSTM
is here as a documented next step for anyone who wants a sequence model.

Install the optional extra first (this pulls in CPU PyTorch, which is several
hundred MB):

    pip install -r ml/requirements-lstm.txt
    PYTHONPATH=. python -m ml.train --model lstm

The autoencoder is trained only on normal windows (reconstruction error is
the anomaly score). A threshold is taken from the training-error distribution,
same idea as the Isolation Forest pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ml.synthetic import generate_dataset
from ml.train import (
    REPO_ROOT,
    EvalReport,
    metrics as _metrics,
    _recall_by_label,
    _split_samples,
    save_report,
)


DEFAULT_LSTM_PATH = REPO_ROOT / "backend" / "models" / "lstm_autoencoder.pt"


def _require_torch():
    try:
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset
    except ImportError as exc:
        raise SystemExit(
            "PyTorch is not installed. The LSTM autoencoder is optional and "
            "intentionally not in the default requirements (it is heavy). "
            "Install it with:\n\n"
            "    pip install -r ml/requirements-lstm.txt\n\n"
            "Then re-run `python -m ml.train --model lstm`. The default "
            "`make train` Isolation Forest does not need PyTorch."
        ) from exc
    return torch, nn, DataLoader, TensorDataset


def _window_matrix(samples, scaler) -> np.ndarray:
    """Shape (n_windows, window_size, n_features) using per-step feature snapshots.

    We reuse `extract_features` on rolling prefixes so the LSTM sees a sequence
    of the same feature space the Isolation Forest uses, just unrolled in time.
    For simplicity and to keep this optional path light, we instead pass the
    raw (cpu, mem_mib, restarts, net rates) sequences — see `_raw_sequences`.
    """
    raise NotImplementedError


def _raw_sequences(samples) -> np.ndarray:
    from ml.features import MIB

    sequences = []
    for sample in samples:
        rows = []
        points = sample.points
        start_rx = points[0].net_rx_bytes
        start_tx = points[0].net_tx_bytes
        start_ts = points[0].ts
        for point in points:
            minutes = max((point.ts - start_ts) / 60.0, 1.0 / 60.0)
            limit = point.memory_limit_bytes
            ratio = (point.memory_bytes / limit) if limit else 0.0
            rows.append(
                [
                    point.cpu,
                    point.memory_bytes / MIB,
                    min(max(ratio, 0.0), 2.0),
                    point.restarts,
                    ((point.net_rx_bytes - start_rx) / MIB) / minutes,
                    ((point.net_tx_bytes - start_tx) / MIB) / minutes,
                ]
            )
        sequences.append(rows)
    return np.asarray(sequences, dtype=np.float32)


class LSTMAutoencoder:
    """Thin wrapper so the rest of the file can type around the nn.Module."""


def _build_model(nn, n_features: int, hidden: int = 16, layers: int = 1):
    class _LSTMAutoencoder(nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder = nn.LSTM(n_features, hidden, num_layers=layers, batch_first=True)
            self.decoder = nn.LSTM(hidden, hidden, num_layers=layers, batch_first=True)
            self.out = nn.Linear(hidden, n_features)

        def forward(self, x):
            encoded, _ = self.encoder(x)
            # Repeat the last hidden state as the decoder input.
            last = encoded[:, -1:, :].repeat(1, x.size(1), 1)
            decoded, _ = self.decoder(last)
            return self.out(decoded)

    return _LSTMAutoencoder()


def _errors(torch, model, data: np.ndarray, batch_size: int = 64) -> np.ndarray:
    model.eval()
    scores = []
    tensor = torch.from_numpy(data)
    with torch.no_grad():
        for start in range(0, len(tensor), batch_size):
            batch = tensor[start : start + batch_size]
            recon = model(batch)
            mse = ((recon - batch) ** 2).mean(dim=(1, 2))
            scores.append(mse.cpu().numpy())
    return np.concatenate(scores) if scores else np.zeros(0, dtype=np.float64)


def train_and_save_lstm(args) -> int:
    torch, nn, DataLoader, TensorDataset = _require_torch()

    samples = generate_dataset(
        n_normal_series=args.n_normal_series,
        n_anomaly_series=args.n_anomaly_series,
        seed=args.seed,
    )
    train_normal, test = _split_samples(samples, seed=args.seed)
    x_train = _raw_sequences(train_normal)
    x_test = _raw_sequences(test)
    y_test = np.array([1 if s.is_anomaly else 0 for s in test], dtype=np.int32)

    mean = x_train.reshape(-1, x_train.shape[-1]).mean(axis=0)
    std = x_train.reshape(-1, x_train.shape[-1]).std(axis=0)
    std = np.where(std < 1e-6, 1.0, std)
    x_train_n = (x_train - mean) / std
    x_test_n = (x_test - mean) / std

    model = _build_model(nn, n_features=x_train.shape[-1])
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    loader = DataLoader(
        TensorDataset(torch.from_numpy(x_train_n)),
        batch_size=32,
        shuffle=True,
    )
    model.train()
    epochs = 8
    for _ in range(epochs):
        for (batch,) in loader:
            opt.zero_grad()
            recon = model(batch)
            loss = ((recon - batch) ** 2).mean()
            loss.backward()
            opt.step()

    train_err = _errors(torch, model, x_train_n)
    threshold = float(np.percentile(train_err, args.threshold_percentile))
    test_err = _errors(torch, model, x_test_n)
    y_pred = (test_err >= threshold).astype(np.int32)
    metrics = _metrics(y_test, y_pred)

    output = Path(getattr(args, "lstm_output", DEFAULT_LSTM_PATH))
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dict": model.state_dict(),
            "mean": mean,
            "std": std,
            "threshold": threshold,
            "n_features": x_train.shape[-1],
            "window_size": x_train.shape[1],
            "version": "lstm-0.1",
        },
        output,
    )

    report = EvalReport(
        model="lstm_autoencoder",
        artifact_version="lstm-0.1",
        seed=args.seed,
        trained_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        n_estimators=0,
        contamination="n/a",
        threshold_percentile=args.threshold_percentile,
        threshold=threshold,
        n_train_normal=int(len(train_normal)),
        n_test_normal=int(sum(1 for s in test if not s.is_anomaly)),
        n_test_anomaly=int(sum(1 for s in test if s.is_anomaly)),
        precision=float(metrics["precision"]),
        recall=float(metrics["recall"]),
        f1=float(metrics["f1"]),
        accuracy=float(metrics["accuracy"]),
        true_negatives=int(metrics["true_negatives"]),
        false_positives=int(metrics["false_positives"]),
        false_negatives=int(metrics["false_negatives"]),
        true_positives=int(metrics["true_positives"]),
        recall_by_label={k: float(v) for k, v in _recall_by_label(test, y_pred).items()},
        feature_names=["cpu", "mem_mib", "mem_to_limit_ratio", "restarts", "net_rx", "net_tx"],
        notes=(
            "Optional LSTM autoencoder, trained on synthetic normal windows only. "
            "Not served by the API; Isolation Forest is the default model."
        ),
    )
    # Don't overwrite the Isolation Forest report by default.
    report_json = REPO_ROOT / "ml" / "reports" / "metrics-lstm.json"
    report_md = REPO_ROOT / "ml" / "reports" / "metrics-lstm.md"
    save_report(report, report_json, report_md)
    print(f"Wrote LSTM model to {output}")
    print(f"Wrote report to {report_json}")
    print(
        f"precision={report.precision:.3f} recall={report.recall:.3f} f1={report.f1:.3f}"
    )
    print(
        "This model is optional and is not loaded by the API. "
        "Serve Isolation Forest (`make train`) for the copilot."
    )
    return 0
