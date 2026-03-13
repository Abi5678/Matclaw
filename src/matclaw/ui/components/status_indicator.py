"""Pro-style status gauge: IDE-like Engine Link indicator."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st


def draw_status_gauge(status: str = "Connected") -> None:
    """
    Pro-style gauge: green when Connected, red otherwise.
    Feels like an IDE status panel rather than a web form.
    """
    connected = status == "Connected"
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=1 if connected else 0,
            title={"text": "Engine Link"},
            gauge={
                "axis": {"range": [0, 1], "tickvals": []},
                "bar": {"color": "#00FF41" if connected else "#FF4136"},
                "bgcolor": "white",
                "steps": [{"range": [0, 1], "color": "#1E1E1E"}],
            },
        )
    )
    fig.update_layout(
        height=150,
        margin=dict(l=10, r=10, t=30, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        font={"color": "#e2e8f0"},
    )
    st.sidebar.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def render_connection_status(
    *,
    connected: bool,
    status_label: str,
    session_name: str | None,
    shared_sessions: list[str],
) -> None:
    """Render Pro-style MATLAB connection status in the sidebar."""
    st.sidebar.subheader("Connection Status")
    draw_status_gauge(status=status_label)
    st.sidebar.caption(f"Session: `{session_name or 'auto'}`")

    if shared_sessions:
        st.sidebar.caption("Shared sessions:")
        for s in shared_sessions[:5]:
            st.sidebar.markdown(f"`{s}`")
        if len(shared_sessions) > 5:
            st.sidebar.caption(f"... +{len(shared_sessions) - 5} more")
    else:
        st.sidebar.caption("No shared MATLAB sessions detected.")
