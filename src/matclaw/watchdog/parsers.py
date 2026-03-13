"""Load .mat and .csv files into normalized structures for ingestion."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from scipy.io import loadmat as _loadmat
except ImportError:
    _loadmat = None

try:
    import pandas as pd
except ImportError:
    pd = None


def load_mat(path: str | Path) -> dict[str, Any]:
    """
    Load a .mat file and return a summary suitable for metadata storage.
    Does not load full arrays into memory for large files; summarizes keys and shapes.
    """
    path = Path(path)
    if not path.suffix.lower() == ".mat":
        raise ValueError(f"Not a .mat file: {path}")
    if _loadmat is None:
        raise RuntimeError("scipy is required to load .mat files")

    raw = _loadmat(str(path), struct_as_record=False, squeeze_me=True)
    # Drop MATLAB internal keys
    out = {}
    for k, v in raw.items():
        if k.startswith("__"):
            continue
        try:
            if hasattr(v, "shape"):
                out[k] = {"type": "array", "shape": [int(x) for x in v.shape], "dtype": str(type(v).__name__)}
            else:
                out[k] = {"type": type(v).__name__, "value": str(v)[:200]}
        except Exception:
            out[k] = {"type": "unknown"}
    return {"path": str(path), "keys": list(out.keys()), "summary": out}


def load_csv(path: str | Path) -> dict[str, Any]:
    """Load a .csv and return shape and column summary (no large data)."""
    path = Path(path)
    if path.suffix.lower() != ".csv":
        raise ValueError(f"Not a .csv file: {path}")
    if pd is None:
        raise RuntimeError("pandas is required to load .csv files")

    df = pd.read_csv(path, nrows=0)
    # Get full shape with a minimal read
    with path.open() as f:
        nrows = sum(1 for _ in f) - 1  # minus header
    cols = list(df.columns)
    return {
        "path": str(path),
        "rows": max(0, nrows),
        "columns": cols,
        "column_count": len(cols),
    }
