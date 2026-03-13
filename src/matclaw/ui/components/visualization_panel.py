"""VisualizationPanel: persistent Latest Plot container with Full Screen toggle."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import streamlit as st


def render_visualization_panel(
    plot_container: st.delta_generator.DeltaGenerator | None = None,
) -> None:
    """
    Render the Latest Plot in a persistent st.empty() container.
    Uses st.session_state.latest_plot_path (PNG) or latest_plotly_fig (Plotly).
    Instantly updates when update_latest_plot() is called.
    """
    if "latest_plot_path" not in st.session_state:
        st.session_state.latest_plot_path = None
    if "latest_plotly_fig" not in st.session_state:
        st.session_state.latest_plotly_fig = None
    if "viz_full_screen" not in st.session_state:
        st.session_state.viz_full_screen = False

    def _clear_plot() -> None:
        st.session_state.latest_plot_path = None
        st.session_state.latest_plotly_fig = None
        st.session_state.viz_full_screen = False
        st.rerun()

    def _render() -> None:
        st.subheader("Latest Plot")
        has_plot = bool(st.session_state.latest_plot_path or st.session_state.latest_plotly_fig)
        col1, col2, col3 = st.columns([1, 1, 1])
        with col1:
            fullscreen_label = "Exit Full Screen" if st.session_state.viz_full_screen else "Full Screen"
            if st.button(fullscreen_label, key="viz_fullscreen_btn", disabled=not has_plot):
                st.session_state.viz_full_screen = not st.session_state.viz_full_screen
                st.rerun()
        with col2:
            if st.button("Close Plot", key="viz_clear_btn", disabled=not has_plot, type="primary"):
                _clear_plot()
        with col3:
            if has_plot:
                st.caption("Click to remove")

        placeholder = st.empty()
        with placeholder.container():
            _render_plot_content(
                st.session_state.latest_plot_path,
                st.session_state.latest_plotly_fig,
                st.session_state.viz_full_screen,
            )

    # plot_container may be st (module) which doesn't support 'with'
    if plot_container is not None and hasattr(plot_container, "__enter__"):
        with plot_container:
            _render()
    else:
        _render()


def _render_plot_content(
    plot_path: str | Path | None,
    plotly_fig: Any,
    full_screen: bool,
) -> None:
    """Render PNG or Plotly in the placeholder."""
    if plot_path and Path(plot_path).is_file():
        if full_screen:
            # Full-screen overlay with close hint (user must click Exit Full Screen or Close Plot above)
            with open(plot_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            st.markdown(
                f"""
                <div id="viz-overlay" style="
                    position:fixed;top:60px;left:0;right:0;bottom:0;
                    background:rgba(15,23,42,0.98);z-index:9999;
                    display:flex;flex-direction:column;align-items:center;justify-content:center;
                    padding:24px;
                ">
                    <p style="color:#94a3b8;font-size:0.85rem;margin-bottom:8px;">Scroll up and click "Close Plot" to dismiss</p>
                    <img src="data:image/png;base64,{b64}"
                         style="max-width:95vw;max-height:85vh;object-fit:contain;">
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.image(str(plot_path), use_container_width=True)
            if st.button("Close Plot", key="viz_clear_below", type="primary"):
                st.session_state.latest_plot_path = None
                st.session_state.latest_plotly_fig = None
                st.rerun()
    elif plotly_fig is not None:
        st.plotly_chart(plotly_fig, use_container_width=True)
        if st.button("Close Plot", key="viz_clear_plotly", type="primary"):
            st.session_state.latest_plot_path = None
            st.session_state.latest_plotly_fig = None
            st.rerun()
    else:
        st.info("No plot yet. Run MATLAB code that creates a figure (e.g. `figure; plot(1:10)`).")


def update_latest_plot(path: str | Path | None = None, plotly_fig: Any = None) -> None:
    """Update the latest plot for the VisualizationPanel. Container updates on next rerun."""
    if path is not None:
        st.session_state.latest_plot_path = str(path)
    if plotly_fig is not None:
        st.session_state.latest_plotly_fig = plotly_fig
