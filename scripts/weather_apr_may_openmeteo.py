#!/usr/bin/env python3
"""
Fetch daily max temperatures (Apr–May) from Open-Meteo archive API and write an
interactive Plotly heatmap under ./plots/ (repo root as cwd).

Designed for MatClaw `run_python` / force_runtime: stdout is human-readable;
the HTML artifact is picked up as a /plots/... URL by PythonRuntime.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import plotly.graph_objects as go

# NYC — change if you like (Open-Meteo: free, no API key)
LAT, LON = 40.7128, -74.0060
YEAR = 2024
TZ = "America/New_York"


def main() -> None:
    start = f"{YEAR}-04-01"
    end = f"{YEAR}-05-31"
    q = (
        "https://archive-api.open-meteo.com/v1/archive?"
        f"latitude={LAT}&longitude={LON}"
        f"&start_date={start}&end_date={end}"
        "&daily=temperature_2m_max"
        f"&timezone={urllib.parse.quote(TZ)}"
    )
    with urllib.request.urlopen(q, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    daily = payload.get("daily") or {}
    dates = daily.get("time") or []
    temps = daily.get("temperature_2m_max") or []
    if len(dates) != len(temps) or not dates:
        raise SystemExit("Unexpected Open-Meteo response shape")

    by_month: dict[str, list[float | None]] = {"April": [np.nan] * 31, "May": [np.nan] * 31}
    for d, t in zip(dates, temps, strict=False):
        month = int(d[5:7])
        day = int(d[8:10])
        if month == 4:
            by_month["April"][day - 1] = float(t)
        elif month == 5:
            by_month["May"][day - 1] = float(t)

    z = np.array([by_month["April"], by_month["May"]], dtype=float)
    x_labels = [str(i) for i in range(1, 32)]
    custom = np.array(
        [
            [f"{m} {i}: {v:.1f} °C" if np.isfinite(v) else f"{m} {i}: n/a"
            for i, v in enumerate(row, start=1)
            ]
            for m, row in zip(["April", "May"], z, strict=False)
        ]
    )

    fig = go.Figure(
        data=go.Heatmap(
            z=z,
            x=x_labels,
            y=["April", "May"],
            text=custom,
            hovertemplate="%{text}<extra></extra>",
            colorscale="Viridis",
            colorbar=dict(title="°C max"),
        )
    )
    fig.update_layout(
        title=f"Daily max temperature (Open-Meteo) — Apr–May {YEAR}<br>"
        f"({LAT}, {LON}) · {TZ}",
        xaxis_title="Day of month",
        yaxis_title="Month",
        margin=dict(l=80, r=20, t=80, b=60),
    )

    out_dir = Path("plots")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"weather_heatmap_apr_may_{YEAR}_nyc.html"
    fig.write_html(str(out), include_plotlyjs="cdn", full_html=True)
    print(f"OK: wrote interactive heatmap → {out.resolve()}")
    print("Open in MatClaw UI as a plot attachment, or visit /plots/ on the API host.")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as e:
        raise SystemExit(f"Network error talking to Open-Meteo: {e}") from e
