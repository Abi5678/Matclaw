"""Vision Gallery: display .png artifacts from metadata in a grid."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st


def _extract_png_paths(metadata: dict, document: str) -> list[str]:
    """Extract .png file paths from artifact metadata and document."""
    paths: list[str] = []
    for k, v in (metadata or {}).items():
        if not isinstance(v, str):
            continue
        if v.lower().endswith(".png") and Path(v).exists():
            paths.append(v)
        if k in ("artifact_paths", "plot_paths", "image_paths") and v.strip().startswith("["):
            try:
                parsed = json.loads(v)
                for p in parsed if isinstance(parsed, list) else []:
                    if isinstance(p, str) and p.lower().endswith(".png") and Path(p).exists():
                        paths.append(p)
            except json.JSONDecodeError:
                pass
    if document and ".png" in document:
        for part in document.split():
            if part.lower().endswith(".png"):
                p = part.strip("[]()\"'")
                if Path(p).exists():
                    paths.append(p)
    return list(dict.fromkeys(paths))


def render_vision_gallery(artifacts: list[dict]) -> None:
    """Render a grid of .png artifacts from the given artifact list."""
    all_paths: list[tuple[str, str]] = []
    for item in artifacts:
        meta = item.get("metadata") or {}
        doc = item.get("document") or ""
        paths = _extract_png_paths(meta, doc)
        label = meta.get("key") or meta.get("skill") or "artifact"
        for p in paths:
            all_paths.append((p, label))

    if not all_paths:
        st.info("No .png artifacts found in research history. Vision Gallery will populate when skills store plot paths.")
        return

    st.subheader("Vision Gallery")
    cols = st.columns(min(3, len(all_paths)) or 1)
    for i, (path, label) in enumerate(all_paths):
        with cols[i % len(cols)]:
            try:
                st.image(path, caption=Path(path).name, use_container_width=True)
            except Exception:
                st.caption(f"Could not load: {path}")
