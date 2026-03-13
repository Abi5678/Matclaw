"""Engineering summary callout: high-visibility display for Vision Analyst output."""

from __future__ import annotations

import html
import streamlit as st


def render_engineering_callout(summary: str, title: str = "Vision Analyst Summary") -> None:
    """Render the Vision Analyst's engineering summary in a high-visibility callout box."""
    if not summary or not summary.strip():
        st.info("No engineering summary available for this artifact.")
        return
    safe_summary = html.escape(summary.strip()).replace("\n", "<br>")
    safe_title = html.escape(title)
    st.markdown(
        f"""
        <div style="
            background: linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%);
            border-left: 4px solid #3b82f6;
            padding: 1rem 1.25rem;
            margin: 1rem 0;
            border-radius: 8px;
            color: #f8fafc;
            font-size: 1rem;
            line-height: 1.6;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2);
        ">
            <strong style="color: #93c5fd;">{safe_title}</strong>
            <p style="margin: 0.75rem 0 0 0;">{safe_summary}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
