"""Kube Copilot backend: diagnose unhealthy Kubernetes pods with an LLM."""

from __future__ import annotations

import sys
from pathlib import Path

# The training package lives at the repo root (`ml/`). Tests, uvicorn, and the
# Docker image all import it as `ml`, so make sure that root is on sys.path.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

__version__ = "0.4.0"
