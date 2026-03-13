"""Sidebar section listing all available MatClaw skills."""

from __future__ import annotations

import streamlit as st

from src.matclaw.skills import get_skill_instructions, list_skills


# Short descriptions for display (fallback when SKILL.md is long)
SKILL_DESCRIPTIONS: dict[str, str] = {
    "workspace_auditor": "Scan MATLAB workspace and license state",
    "pid_optimizer": "Tune Kp, Ki, Kd gains for 2nd-order plant",
    "report_generator": "Generate project report from artifacts",
}


def render_skills_sidebar() -> None:
    """Render all available skills in the sidebar."""
    skills = list_skills()
    if not skills:
        st.sidebar.subheader("Skills")
        st.sidebar.caption("No skills installed.")
        return

    st.sidebar.subheader("Skills")
    for name in skills:
        desc = SKILL_DESCRIPTIONS.get(name)
        if not desc:
            instr = get_skill_instructions(name)
            desc = (instr or "").split("\n")[0][:60] if instr else name
        st.sidebar.markdown(f"**{name}**")
        st.sidebar.caption(desc)
