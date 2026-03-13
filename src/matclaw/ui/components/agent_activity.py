"""Agent Activity sidebar: real-time RPI phase updates (Research, Plan, Execute)."""

from __future__ import annotations

import html
import random
import streamlit as st

# Snarky status messages (OpenClaw-style)
_SNARK_BY_PHASE: dict[str, list[str]] = {
    "research": [
        "Sifting through your workspace clutter...",
        "Looking for toolboxes you probably don't have...",
        "Auditing variables. Try not to have 47 copies of x.",
    ],
    "plan": [
        "Drafting a plan that actually obeys physics...",
        "Trying to fix this 'logic' of yours...",
        "Designing something that won't explode. Hopefully.",
    ],
    "execute": [
        "Launching the simulation. Stand back.",
        "Sending code to MATLAB. Fingers crossed.",
        "Executing. No refunds if it crashes.",
    ],
}


def get_snarky_title(phase: str, status: str) -> str:
    """Return snarky subtitle when this phase is active, else the phase name."""
    if status == phase:
        return random.choice(_SNARK_BY_PHASE.get(phase, [phase]))
    return phase.title()


def _format_plan_as_latex(plan_data: dict, skill_name: str, skill_kwargs: dict | None) -> str:
    """Format plan_data as LaTeX for display."""
    if not plan_data:
        return ""
    skill_kwargs = skill_kwargs or {}
    parts: list[str] = []

    if skill_name == "run_matlab" or plan_data.get("action") == "run_matlab":
        return r"\text{Action: } \texttt{run\_matlab} \quad \text{(see Execute)}"

    if skill_name == "pid_optimizer":
        gains = plan_data.get("gains") or skill_kwargs
        Kp = gains.get("Kp", 0)
        Ki = gains.get("Ki", 0)
        Kd = gains.get("Kd", 0)
        done = plan_data.get("done", False)
        metrics = plan_data.get("metrics") or {}
        rt = metrics.get("rise_time", "—")
        os = metrics.get("overshoot_pct", "—")
        parts.append(rf"K_p = {Kp:.3g}, \; K_i = {Ki:.3g}, \; K_d = {Kd:.3g}")
        if metrics:
            parts.append(rf"t_r = {rt} \text{{ s}}, \; M_p = {os}\%")
        if done:
            parts.append(r"\text{Stable}")
        else:
            parts.append(
                r"K_{p,\mathrm{new}} \leftarrow 1.2 K_p; \; K_d \leftarrow K_d + 0.1"
            )
    else:
        action = plan_data.get("action", "run_skill")
        parts.append(rf"\text{{Action: }} \texttt{{{action}}}")
        parts.append(rf"\text{{Skill: }} \texttt{{{skill_name}}}")
        lessons = plan_data.get("lessons_incorporated") or []
        if lessons:
            parts.append(rf"\text{{Lessons: }} {len(lessons)}")

    return r" \quad ".join(parts)


def _card_content_html(text: str, max_len: int = 500) -> str:
    """Escape and truncate text for HTML card content."""
    if not text:
        return ""
    escaped = html.escape(text[:max_len] + ("..." if len(text) > max_len else ""))
    return escaped.replace("\n", "<br>")


def _code_block_html(code: str, max_len: int = 500) -> str:
    """Format code as HTML pre/code block."""
    if not code:
        return ""
    escaped = html.escape(code[:max_len] + ("..." if len(code) > max_len else ""))
    return f'<pre class="agent-code"><code>{escaped}</code></pre>'


def format_matlab_call_as_code(function: str, args: list, kwargs: dict | None = None) -> str:
    """Format a MATLAB call as executable code string."""
    kwargs = kwargs or {}
    arg_strs = [repr(a) for a in args]
    kw_strs = [f"{k}={repr(v)}" for k, v in kwargs.items()]
    all_args = ", ".join(arg_strs + kw_strs)
    return f"{function}({all_args})"


def render_agent_activity_sidebar(
    research_data: dict | None = None,
    plan_data: dict | None = None,
    plan_skill_name: str = "",
    plan_skill_kwargs: dict | None = None,
    execute_code: str | None = None,
    status: str = "idle",
    nemotron_thoughts: dict | None = None,
) -> None:
    """
    Render the Agent Activity sidebar with styled cards (blue=Research, amber=Plan, green=Execute).
    """
    thoughts = nemotron_thoughts or {}
    plan_data = plan_data or {}

    st.sidebar.subheader("Agent Activity")

    with st.sidebar.status(
        "Idle" if status == "idle" else "Running...",
        state="running" if status in ("research", "plan", "execute") else "complete",
    ):
        # Research card (blue glow)
        research_text = thoughts.get("research", "").strip()
        toolboxes = (research_data or {}).get("matlab_toolboxes") or []
        research_parts = []
        if research_text:
            research_parts.append(_card_content_html(research_text, 400))
        if toolboxes:
            research_parts.append("<ul>" + "".join(f"<li>{html.escape(tb)}</li>" for tb in toolboxes[:15]) + "</ul>")
            if len(toolboxes) > 15:
                research_parts.append(f"<small>... and {len(toolboxes) - 15} more</small>")
        research_content = "".join(research_parts) or "<em>No research data yet.</em>"
        research_title = get_snarky_title("research", status)
        st.sidebar.markdown(
            f'<div class="agent-activity-card research"><div class="card-title">{html.escape(research_title)}</div>'
            f'<div class="card-content">{research_content}</div></div>',
            unsafe_allow_html=True,
        )

        # Plan card (amber glow)
        plan_text = thoughts.get("plan", "").strip()
        if plan_data.get("action") == "run_matlab" and plan_data.get("code"):
            plan_label = '<small>Action: run_matlab</small><br>'
            plan_content = _code_block_html(plan_data["code"])
        elif plan_text:
            plan_label = ""
            plan_content = _code_block_html(plan_text)
        else:
            latex = _format_plan_as_latex(plan_data, plan_skill_name, plan_skill_kwargs)
            plan_label = ""
            plan_content = f'<div class="card-content">{html.escape(latex) if latex else "<em>No plan data.</em>"}</div>'
        plan_title = get_snarky_title("plan", status)
        st.sidebar.markdown(
            f'<div class="agent-activity-card plan"><div class="card-title">{html.escape(plan_title)}</div>'
            f'{plan_label}{plan_content}</div>',
            unsafe_allow_html=True,
        )

        # Execute card (green glow)
        execute_text = thoughts.get("execute", "").strip()
        if execute_code:
            exec_content = _code_block_html(execute_code)
        elif execute_text:
            exec_content = f'<div class="card-content">{_card_content_html(execute_text, 300)}</div>'
        else:
            exec_content = '<div class="card-content"><em>No MATLAB code sent yet.</em></div>'
        exec_title = get_snarky_title("execute", status)
        st.sidebar.markdown(
            f'<div class="agent-activity-card execute"><div class="card-title">{html.escape(exec_title)}</div>'
            f'{exec_content}</div>',
            unsafe_allow_html=True,
        )
