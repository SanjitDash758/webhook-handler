"""
Prometheus multiprocess initialization.

Why: prometheus_client's multiprocess mode requires a shared
directory (PROMETHEUS_MULTIPROC_DIR) where each worker writes
its metrics. Without this, worker metrics are per-process and
never appear in the /metrics endpoint.
"""

import os
import tempfile
from pathlib import Path


def setup_multiprocess_dir() -> str:
    configured = os.getenv("PROMETHEUS_MULTIPROC_DIR")

    if configured:
        path = Path(configured)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    # Fallback: a temp directory under the system temp folder.
    path = Path(tempfile.gettempdir()) / "prometheus_multiproc"
    path.mkdir(parents=True, exist_ok=True)
    os.environ["PROMETHEUS_MULTIPROC_DIR"] = str(path)
    return str(path)