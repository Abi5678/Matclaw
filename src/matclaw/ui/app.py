from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is on path (for streamlit run src/matclaw/ui/app.py)
_root = Path(__file__).resolve().parents[3]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

# Load .env so NVIDIA_API_KEY etc. are available for Conversational Lab
try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env")
except ImportError:
    pass

from dataclasses import dataclass

import pandas as pd
import plotly.express as px
import streamlit as st

from src.matclaw.config import MatClawSettings
from src.matclaw.llm.nemotron_client import NemotronClient
from src.matclaw.memory.memory_manager import MemoryManager
from src.matclaw.ui.components import (
    render_agent_activity_sidebar,
    render_connection_status,
    render_skills_sidebar,
    render_engineering_callout,
    render_hybrid_rpi_panel,
    render_interactive_tuner,
    render_vision_gallery,
    render_visualization_panel,
    update_latest_plot,
)

try:
    import matlab.engine  # type: ignore[attr-defined]
except Exception:
    matlab = None  # type: ignore[assignment]
else:
    matlab = matlab  # type: ignore[assignment]


@dataclass
class MATLABSessionState:
    connected: bool
    status_label: str
    session_name: str | None
    shared_sessions: list[str]
    detail: str


class MATLABSessionManager:
    """Read-only helper for discovering shared MATLAB engine sessions."""

    def __init__(self, settings: MatClawSettings | None = None) -> None:
        self.settings = settings or MatClawSettings()

    def get_state(self) -> MATLABSessionState:
        session_name = self.settings.matlab.session_name
        if matlab is None:
            return MATLABSessionState(
                connected=False,
                status_label="Disconnected",
                session_name=session_name,
                shared_sessions=[],
                detail="`matlab.engine` is not installed in the active Python environment.",
            )

        try:
            shared_sessions = list(matlab.engine.find_matlab())
        except Exception as exc:
            return MATLABSessionState(
                connected=False,
                status_label="Disconnected",
                session_name=session_name,
                shared_sessions=[],
                detail=f"Failed to query shared MATLAB sessions: {exc}",
            )

        if session_name:
            connected = session_name in shared_sessions
            detail = (
                f"Shared session `{session_name}` is available."
                if connected
                else f"Configured shared session `{session_name}` was not found."
            )
        else:
            connected = bool(shared_sessions)
            detail = (
                "At least one shared MATLAB session is available."
                if connected
                else "No shared MATLAB sessions are currently available."
            )

        return MATLABSessionState(
            connected=connected,
            status_label="Connected" if connected else "Disconnected",
            session_name=session_name,
            shared_sessions=shared_sessions,
            detail=detail,
        )


def _build_research_dataframe(artifacts: list[dict]) -> pd.DataFrame:
    """Build a display DataFrame from MemoryManager query results."""
    rows = []
    for i, item in enumerate(artifacts):
        meta = item.get("metadata") or {}
        doc = item.get("document") or ""
        key = meta.get("key", "")
        skill = meta.get("skill", "")
        summary = meta.get("summary") or meta.get("message") or doc[:200]
        file_path = meta.get("file") or meta.get("report_path") or ""
        vision_summary = meta.get("vision_summary") or meta.get("analysis") or ""
        rows.append({
            "index": i,
            "Key": key,
            "Skill": skill,
            "Summary": str(summary)[:120] + ("..." if len(str(summary)) > 120 else ""),
            "File": str(file_path)[:80] + ("..." if len(str(file_path)) > 80 else ""),
            "Vision Summary": vision_summary[:80] + ("..." if len(vision_summary) > 80 else "") if vision_summary else "",
            "_vision_full": vision_summary,
            "_raw": item,
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["index", "Key", "Skill", "Summary", "File", "Vision Summary", "_vision_full", "_raw"])


def _render_research_history_tab(memory_manager: MemoryManager) -> None:
    """Render the Research History tab: searchable DataFrame, Vision Gallery, row selection callout."""
    try:
        artifacts = memory_manager.query_context("recent artifacts parameters lessons learned", n_results=50)
    except Exception as exc:
        st.error(f"Failed to load research history: {exc}")
        return

    if not artifacts:
        st.info("No research history yet. Artifacts will appear here after skill runs and Sentry triggers.")
        return

    df = _build_research_dataframe(artifacts)
    if df.empty:
        st.info("No artifacts to display.")
        return

    display_cols = ["Key", "Skill", "Summary", "File"]
    search = st.text_input("Search artifacts", placeholder="Filter by key, skill, summary...")
    if search:
        mask = df["Key"].astype(str).str.contains(search, case=False, na=False)
        mask |= df["Skill"].astype(str).str.contains(search, case=False, na=False)
        mask |= df["Summary"].astype(str).str.contains(search, case=False, na=False)
        mask |= df["File"].astype(str).str.contains(search, case=False, na=False)
        df = df[mask].reset_index(drop=True)

    st.dataframe(df[display_cols], width="stretch", hide_index=True)

    if df.empty:
        return

    st.subheader("Select an artifact")
    options = [f"{row['Key']} — {row['Skill']}" for _, row in df.iterrows()]
    selected = st.selectbox(
        "Click to view Vision Analyst summary",
        range(len(df)),
        format_func=lambda i: options[i] if i < len(options) else "",
        key="research_history_select",
    )

    if selected is not None and 0 <= int(selected) < len(df):
        row = df.iloc[int(selected)]
        vision_full = row.get("_vision_full") or ""
        render_engineering_callout(
            vision_full if vision_full else row.get("Summary", ""),
            title="Vision Analyst Summary" if vision_full else "Artifact Summary",
        )

    st.divider()
    render_vision_gallery(artifacts)


def _inject_theme() -> None:
    """Inject custom dark-mode CSS and Bento layout styles."""
    css_path = Path(__file__).resolve().parent / "theme.css"
    if css_path.is_file():
        st.markdown(
            f"<style>{css_path.read_text(encoding='utf-8')}</style>",
            unsafe_allow_html=True,
        )


def _render_public_landing() -> None:
    """Render a public-style landing screen inside Streamlit."""
    st.markdown(
        """
<section class="matclaw-hero">
  <span class="matclaw-pill">NEW · Landing + Control Plane</span>
  <h1>THE AI THAT ACTUALLY DOES THINGS.</h1>
  <p class="sub">
    MatClaw runs persistent Research → Plan → Implement loops for engineering workflows.
    Start from this public home, then enter the app to run hybrid prompts, MATLAB actions,
    memory queries, and audited execution.
  </p>
</section>
        """,
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns([1.2, 1])
    with c1:
        if st.button("Open MatClaw App", type="primary", use_container_width=True):
            st.session_state.ui_view = "app"
            st.rerun()
    with c2:
        st.link_button("View GitHub", "https://github.com/Abi5678/Matclaw", use_container_width=True)

    st.caption(
        "End users: click Open MatClaw App to access the real control plane. "
        "This landing screen is the public entry point."
    )


def _render_control_plane_hero(session_state: MATLABSessionState) -> None:
    """Render app header with same visual language as landing card."""
    st.markdown(
        f"""
<section class="matclaw-hero">
  <span class="matclaw-pill">CONTROL PLANE · LIVE</span>
  <h1>MatClaw · Hybrid RPI Lab</h1>
  <p class="sub">
    Research → Plan → Implement with a hybrid context: voice/intent + code snippet.
    This runs the same <code>run_flow</code> pathway as the Ctrl+Alt+M gateway.
  </p>
  <div class="matclaw-chip-row">
    <div class="matclaw-chip"><div class="label">MATLAB</div><div class="value">{session_state.status_label}</div></div>
    <div class="matclaw-chip"><div class="label">Shared Sessions</div><div class="value">{len(session_state.shared_sessions)}</div></div>
    <div class="matclaw-chip"><div class="label">Mode</div><div class="value">Hybrid RPI</div></div>
  </div>
</section>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="MatClaw · Hybrid RPI Lab", page_icon="🧪", layout="wide")
    _inject_theme()

    if "ui_view" not in st.session_state:
        st.session_state.ui_view = "landing"

    if st.session_state.ui_view == "landing":
        _render_public_landing()
        return

    settings = MatClawSettings()
    session_manager = MATLABSessionManager(settings)
    session_state = session_manager.get_state()

    render_connection_status(
        connected=session_state.connected,
        status_label=session_state.status_label,
        session_name=session_state.session_name,
        shared_sessions=session_state.shared_sessions,
    )

    if "agent_research" not in st.session_state:
        st.session_state.agent_research = None
    if "agent_plan" not in st.session_state:
        st.session_state.agent_plan = None
    if "agent_plan_skill" not in st.session_state:
        st.session_state.agent_plan_skill = ""
    if "agent_plan_kwargs" not in st.session_state:
        st.session_state.agent_plan_kwargs = None
    if "agent_execute_code" not in st.session_state:
        st.session_state.agent_execute_code = None
    if "agent_status" not in st.session_state:
        st.session_state.agent_status = "idle"
    if "nemotron_thoughts" not in st.session_state:
        st.session_state.nemotron_thoughts = None

    render_agent_activity_sidebar(
        research_data=st.session_state.agent_research,
        plan_data=st.session_state.agent_plan,
        plan_skill_name=st.session_state.agent_plan_skill,
        plan_skill_kwargs=st.session_state.agent_plan_kwargs,
        execute_code=st.session_state.agent_execute_code,
        status=st.session_state.agent_status,
        nemotron_thoughts=st.session_state.nemotron_thoughts,
    )

    render_skills_sidebar()

    top_left, top_right = st.columns([5, 1.2])
    with top_right:
        if st.button("Back to Home", type="primary", use_container_width=True):
            st.session_state.ui_view = "landing"
            st.rerun()

    _render_control_plane_hero(session_state)

    # Bento Box 3-column layout
    col_left, col_center, col_right = st.columns([1, 2, 1])

    with col_left:
        with st.container():
            st.caption("Quick Status")
            st.metric("MATLAB", session_state.status_label, delta=None)

    with col_center:
        # VisualizationPanel: persistent Latest Plot (st.empty) + Full Screen toggle
        render_visualization_panel()

        tab0, tab1, tab2, tab3, tab4 = st.tabs(
            [
                "Hybrid RPI",
                "Session Manager",
                "Research History",
                "Interactive Tuner",
                "Conversational Lab",
            ]
        )

        with tab0:
            render_hybrid_rpi_panel(settings)

        with tab1:
            left, right = st.columns([1.2, 1])
            with left:
                st.subheader("MATLAB Session Manager")
                st.write(session_state.detail)

                status_df = pd.DataFrame(
                    [
                        {"check": "MATLAB shared session", "status": session_state.status_label},
                        {"check": "Configured session", "status": session_state.session_name or "auto-discover"},
                        {"check": "Visible shared sessions", "status": str(len(session_state.shared_sessions))},
                    ]
                )
                st.dataframe(status_df, width="stretch", hide_index=True)

                with st.expander("How to expose a shared MATLAB engine", expanded=not session_state.connected):
                    st.code("""
In a MATLAB desktop session, run:

matlab.engine.shareEngine

Or, if you want a stable name:

matlab.engine.shareEngine('MatClawShared')
""".strip(), language="matlab")

            with right:
                chart_df = pd.DataFrame(
                    {
                        "state": ["Connected", "Disconnected"],
                        "value": [1 if session_state.connected else 0, 0 if session_state.connected else 1],
                    }
                )
                fig = px.bar(
                    chart_df,
                    x="state",
                    y="value",
                    color="state",
                    color_discrete_map={"Connected": "#16a34a", "Disconnected": "#dc2626"},
                    title="Shared MATLAB Availability",
                )
                fig.update_layout(showlegend=False, yaxis_title="Status", xaxis_title="")
                st.plotly_chart(fig, width="stretch")

        with tab2:
            memory_manager = MemoryManager(persist_directory=".matclaw_chromadb")
            try:
                memory_manager._ensure_client()
            except Exception:
                pass
            _render_research_history_tab(memory_manager)

        with tab3:
            _render_interactive_tuner_tab(settings)

        with tab4:
            _render_conversational_lab_tab(settings)

    with col_right:
        st.caption("Agent Activity")
        st.caption("See sidebar for Research / Plan / Execute")


def _render_matlab_code_with_copy(code: str) -> None:
    """Render MATLAB code in a syntax-highlighted block with Copy to Clipboard button."""
    import base64
    st.code(code, language="matlab")
    b64 = base64.b64encode(code.encode("utf-8", errors="replace")).decode("ascii")
    st.markdown(
        f'<button data-code="{b64}" onclick="'
        "navigator.clipboard.writeText(atob(this.dataset.code));"
        "this.textContent='Copied!';"
        "setTimeout(()=>this.textContent='Copy to Clipboard', 1500);"
        '" style="'
        "font-size:0.8rem;padding:6px 12px;cursor:pointer;"
        "background:#334155;color:#e2e8f0;border:1px solid #475569;"
        "border-radius:6px;margin-top:4px;"
        '">Copy to Clipboard</button>',
        unsafe_allow_html=True,
    )


def _render_conversational_lab_tab(settings: MatClawSettings) -> None:
    """Render the Conversational Lab tab: chat with Nemotron, run MATLAB / skills / query memory."""
    if "messages" not in st.session_state:
        st.session_state.messages = []

    st.subheader("Conversational Lab")
    st.caption("Chat with the MatClaw Orchestrator. Ask to plot, run skills, or query memory.")

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("code"):
                _render_matlab_code_with_copy(msg["code"])
            if msg.get("tool"):
                st.caption(f"Tool: {msg['tool']}")

    prompt = st.chat_input("Ask MatClaw...")
    pending = st.session_state.get("_conversational_pending")

    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            try:
                # All messages go through Nemotron with pre-flight context (no greeting bypass)
                p = prompt.strip().lower()
                plot_keywords = ("plot", "graph", "boxplot", "figure", "chart", "visualize", "draw")
                simulink_run_keywords = ("simulate", "run model", "run .slx", ".slx")
                simulink_keywords = ("simulink", "sim(", "open_system", "load_system", "simulate", "run model")
                memory_keywords = ("history", "last", "previous", "before", "remember", "past")
                force_analyze_file = (
                    ".m" in p
                    and any(k in p for k in ("check", "fix", "run", "analyze", "access", "debug", "open", "read", "look"))
                    and not any(k in p for k in memory_keywords)
                )
                # Detect "create <name>.m <code>" pattern
                force_create_file = (
                    ".m" in p
                    and any(k in p for k in ("create", "write", "make", "save"))
                    and not force_analyze_file
                )
                force_simulink_runner = (
                    any(k in p for k in simulink_run_keywords)
                    and not any(k in p for k in memory_keywords)
                    and not force_analyze_file
                )
                force_run_matlab = (
                    (any(k in p for k in plot_keywords) or any(k in p for k in simulink_keywords))
                    and not force_simulink_runner
                    and not force_analyze_file
                    and not any(k in p for k in memory_keywords)
                )
                # --- Handle "create <file>.m <code>" directly (no LLM needed) ---
                if force_create_file:
                    result = _handle_create_file(settings, prompt)
                    st.markdown(result)
                    st.session_state.messages.append({"role": "assistant", "content": result, "tool": "create_file"})
                    st.caption("Tool: create_file")
                    # Skip the rest of the handler
                elif True:
                    with st.spinner("Thinking..."):
                        bridge, mm, conversation_history = _get_bridge_memory_and_history(settings)
                        client = NemotronClient(matlab_bridge=bridge, memory_manager=mm)
                        context = _get_chat_context(settings)
                        force_tool = None
                        if force_analyze_file:
                            force_tool = "analyze_file"
                        elif force_simulink_runner:
                            force_tool = "simulink_runner"
                        elif force_run_matlab:
                            force_tool = "run_matlab"
                        action = client.generate_action(
                            prompt,
                            context,
                            force_tool=force_tool,
                            conversation_history=conversation_history,
                        )
                        if bridge is not None and hasattr(bridge, "stop"):
                            try:
                                bridge.stop()
                            except Exception:
                                pass

                    if action.tool == "respond":
                        # Nemotron returned text without a tool call (e.g. smart greeting with lab context)
                        content = action.arguments.get("content", "") or action.arguments.get("message", "")
                        st.markdown(content)
                        st.session_state.messages.append({"role": "assistant", "content": content, "tool": None})
                    elif action.tool == "analyze_file":
                        file_path = action.arguments.get("file_path", "")
                        file_action = action.arguments.get("action", "fix_and_run")
                        st.markdown(f"🔍 Analyzing `{file_path}` (action: {file_action})...")
                        result = _run_analyze_file(settings, file_path, file_action)
                        st.markdown(result)
                        st.session_state.messages.append({"role": "assistant", "content": result, "tool": "analyze_file"})
                        st.caption("Tool: analyze_file")
                    elif action.tool == "run_matlab":
                        code = action.arguments.get("code", "")
                        t = action.thoughts
                        st.session_state.nemotron_thoughts = {
                            "research": getattr(t, "research", "") if t else "",
                            "plan": getattr(t, "plan", "") or code,
                            "execute": getattr(t, "execute", "") if t else "(pending)",
                        }
                        st.session_state._conversational_pending = {
                            "phase": "research",
                            "tool": "run_matlab",
                            "code": code,
                            "user_input": prompt,
                        }
                        st.markdown("Executing MATLAB code...")
                        _render_matlab_code_with_copy(code)
                        _run_matlab_with_agent_activity(settings)
                    elif action.tool == "trigger_skill":
                        skill_name = action.arguments.get("skill_name", "workspace_auditor")
                        skill_kwargs = {k: v for k, v in action.arguments.items() if k != "skill_name"}
                        _run_trigger_skill_with_agent_activity(settings, skill_name, prompt, skill_kwargs=skill_kwargs)
                        result = _get_last_tool_result()
                        st.markdown(result or "Done.")
                        st.session_state.messages.append({"role": "assistant", "content": result or "Done.", "tool": action.tool})
                        st.caption(f"Tool: {action.tool}")
                    elif action.tool == "query_memory":
                        result = _run_query_memory(settings, action.arguments.get("query", prompt))
                        st.markdown(result)
                        st.session_state.messages.append({"role": "assistant", "content": result, "tool": "query_memory"})
                        st.caption("Tool: query_memory")
                    else:
                        # Fallback: respond with content or query memory
                        content = action.arguments.get("content", "") or action.arguments.get("message", "")
                        if content:
                            st.markdown(content)
                            st.session_state.messages.append({"role": "assistant", "content": content, "tool": None})
                        else:
                            result = _run_query_memory(settings, prompt)
                            st.markdown(result)
                            st.session_state.messages.append({"role": "assistant", "content": result, "tool": "query_memory"})
            except Exception as exc:
                st.error(str(exc))
                st.session_state.messages.append({"role": "assistant", "content": f"Error: {exc}", "tool": None})

    elif pending and pending.get("tool") == "run_matlab":
        with st.chat_message("assistant"):
            code = pending.get("code", "")
            if code:
                st.markdown("Executing MATLAB code...")
                _render_matlab_code_with_copy(code)
            _run_matlab_with_agent_activity(settings)
            if "_conversational_pending" not in st.session_state:
                result = _get_last_tool_result()
                code = pending.get("code", "")
                st.markdown(result or "Done.")
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": result or "Done.",
                    "tool": "run_matlab",
                    "code": code,
                })
                st.caption("Tool: run_matlab")


def _get_chat_context(settings: MatClawSettings) -> dict:
    """Build context for Nemotron: available skills + recent artifacts (legacy)."""
    from src.matclaw.skills import list_skills
    ctx: dict = {"available_skills": list_skills()}
    try:
        mm = MemoryManager(persist_directory=".matclaw_chromadb")
        mm._ensure_client()
        results = mm.query_context("recent artifacts parameters", n_results=3)
        ctx["recent_artifacts"] = [r.get("metadata", {}) for r in results]
    except Exception:
        pass
    return ctx


def _get_bridge_memory_and_history(settings: MatClawSettings) -> tuple[object, object, list[dict]]:
    """
    Get MATLAB bridge, memory manager, and conversation history for NemotronClient.
    Returns (matlab_bridge, memory_manager, conversation_history).
    Bridge is started and should be stopped by caller after use.
    """
    bridge = None
    try:
        from src.matclaw.matlab.matlab_bridge import MatlabBridge
        import matlab.engine as _me
        _sessions = _me.find_matlab()
        _session_name = settings.matlab.session_name
        if _session_name and _session_name in _sessions:
            bridge = MatlabBridge(settings=settings.matlab)
        elif _sessions:
            from src.matclaw.config.base_config import MatlabSettings
            bridge = MatlabBridge(settings=MatlabSettings(session_name=_sessions[0], enabled=True))
        else:
            bridge = MatlabBridge(settings=settings.matlab)
        bridge.start()
        if not bridge.is_healthy():
            bridge = None
    except Exception:
        pass

    mm = None
    try:
        mm = MemoryManager(persist_directory=".matclaw_chromadb")
        mm._ensure_client()
    except Exception:
        pass

    all_msgs = st.session_state.get("messages", [])
    prev_msgs = [m for m in all_msgs[:-1] if m.get("role") in ("user", "assistant")]
    history = [{"role": m["role"], "content": m.get("content", "")} for m in prev_msgs[-6:]]
    return bridge, mm, history


def _run_matlab_with_agent_activity(settings: MatClawSettings) -> None:
    """Run MATLAB code with Research/Plan/Execute updates in sidebar (real-time via rerun)."""
    pending = st.session_state.get("_conversational_pending")
    if pending is None or pending.get("tool") != "run_matlab":
        return

    phase = pending.get("phase", "research")

    if phase == "research":
        st.session_state.agent_status = "research"
        research_data: dict = {"matlab_toolboxes": [], "lessons_learned": []}
        try:
            mm = MemoryManager(persist_directory=".matclaw_chromadb")
            mm._ensure_client()
            results = mm.query_context(pending["user_input"], n_results=3)
            research_data["lessons_learned"] = [r.get("metadata", {}) for r in results]
        except Exception:
            pass
        try:
            from src.matclaw.matlab.matlab_bridge import MatlabBridge
            bridge = MatlabBridge(settings=settings.matlab)
            bridge.start()
            if bridge.is_healthy():
                from src.matclaw.core.rpi_executor import _get_matlab_toolboxes
                research_data["matlab_toolboxes"] = _get_matlab_toolboxes(bridge)
        except Exception:
            pass
        st.session_state.agent_research = research_data
        pending["phase"] = "plan"
        st.rerun()

    if phase == "plan":
        st.session_state.agent_status = "plan"
        st.session_state.agent_plan = {"action": "run_matlab", "code": pending["code"]}
        st.session_state.agent_plan_skill = "run_matlab"
        st.session_state.agent_plan_kwargs = {}
        st.session_state.agent_execute_code = pending["code"]
        pending["phase"] = "execute"
        st.rerun()

    if phase == "execute":
        st.session_state.agent_status = "execute"
        code = pending["code"]
        output = ""
        max_fix_retries = 2
        retry_count = pending.get("fix_retry_count", 0)

        try:
            from src.matclaw.matlab.matlab_bridge import MatlabBridge
            from src.matclaw.llm.nemotron_client import NemotronClient
            import matlab.engine as _me
            _sessions = _me.find_matlab()
            _sn = settings.matlab.session_name
            if _sn and _sn in _sessions:
                bridge = MatlabBridge(settings=settings.matlab)
            elif _sessions:
                from src.matclaw.config.base_config import MatlabSettings
                bridge = MatlabBridge(settings=MatlabSettings(session_name=_sessions[0], enabled=True))
            else:
                bridge = MatlabBridge(settings=settings.matlab)
            bridge.start()
            if bridge.is_healthy():
                success, out = bridge.run_matlab_code(code)
                if success:
                    output = out or "(executed successfully)"
                    # Capture figure and update VisualizationPanel
                    fig_dir = Path("data/plots")
                    fig_dir.mkdir(parents=True, exist_ok=True)
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    fig_path = fig_dir / f"fig_{stamp}.png"
                    if bridge.capture_figure(fig_path):
                        update_latest_plot(path=fig_path)
                else:
                    # Self-healing: send error to Nemotron, fix and retry
                    if retry_count < max_fix_retries:
                        client = NemotronClient()
                        fixed = client.fix_matlab_code(out or "Unknown error", code)
                        if fixed and fixed.strip() != code.strip():
                            pending["code"] = fixed.strip()
                            pending["fix_retry_count"] = retry_count + 1
                            st.session_state.agent_execute_code = fixed
                            t = st.session_state.nemotron_thoughts or {}
                            t["execute"] = f"Retry {retry_count + 1}: Nemotron fixed code after error."
                            st.session_state.nemotron_thoughts = t
                            st.rerun()
                            return
                    output = f"Error: {out}"
            else:
                output = "MATLAB bridge not available."
        except Exception as exc:
            output = f"Error: {exc}"
        st.session_state._last_tool_result = output
        st.session_state.agent_status = "complete"
        del st.session_state._conversational_pending


def _format_skill_result_conversational(skill_name: str, result: Any, data: dict) -> str:
    """Convert raw skill result data into a friendly, conversational summary."""
    lines: list[str] = []

    if skill_name == "pid_optimizer":
        metrics = data.get("metrics") or {}
        gains   = data.get("gains") or {}
        rt   = metrics.get("rise_time") or data.get("rise_time") or data.get("rise_time_s")
        over = metrics.get("overshoot_pct") or data.get("overshoot") or data.get("overshoot_pct")
        iters = data.get("iterations", 1)
        stable = data.get("stable", metrics.get("stable", True))
        kp = gains.get("Kp") or gains.get("kp") or data.get("Kp") or data.get("kp")
        ki = gains.get("Ki") or gains.get("ki") or data.get("Ki") or data.get("ki")
        kd = gains.get("Kd") or gains.get("kd") or data.get("Kd") or data.get("kd")

        if stable:
            lines.append("✅ **PID tuning succeeded!** The controller is stable.")
        else:
            lines.append("⚠️ **PID tuning did not converge** to a stable solution.")

        if rt is not None:
            rt_f = float(rt)
            quality = "fast" if rt_f < 0.5 else "moderate" if rt_f < 2.0 else "slow"
            lines.append(f"⏱️ **Rise time:** `{rt_f:.3f} s` — the system reaches its target in {rt_f:.2f} seconds ({quality} response).")

        if over is not None:
            ov_f = float(over)
            if ov_f < 2:
                interp = "nearly perfect — very well damped"
            elif ov_f < 5:
                interp = "excellent — barely overshoots"
            elif ov_f < 10:
                interp = "acceptable — mild overshoot"
            elif ov_f < 20:
                interp = "moderate — consider increasing Kd"
            else:
                interp = "high — system is oscillating, reduce Kp or increase Kd"
            lines.append(f"📈 **Overshoot:** `{ov_f:.2f}%` — {interp}.")

        if iters:
            lines.append(f"🔁 **Converged in {iters} iteration{'s' if int(iters) != 1 else ''}** of the RPI tuning loop.")

        if kp is not None:
            lines.append(f"🎛️ **Final gains:** Kp=`{float(kp):.4f}`, Ki=`{float(ki or 0):.4f}`, Kd=`{float(kd or 0):.4f}`")

    elif skill_name == "workspace_auditor":
        n_vars = len(data.get("variables") or [])
        total_mb = (data.get("total_bytes") or 0) / 1e6
        warnings = data.get("warnings") or []
        lic = data.get("license_inuse") or ""

        lines.append(f"🔬 **Workspace audit complete.** Found **{n_vars} variable{'s' if n_vars != 1 else ''}** using **{total_mb:.1f} MB** of memory.")

        for v in (data.get("variables") or [])[:10]:
            name = v.get("name", "?")
            mb   = (v.get("bytes") or 0) / 1e6
            cls  = v.get("class", "?")
            size_str = f"{mb:.1f} MB" if mb >= 0.1 else f"{int(v.get('bytes', 0))} B"
            lines.append(f"  • **{name}** — {size_str} ({cls})")
        if n_vars > 10:
            lines.append(f"  • *…and {n_vars - 10} more*")

        for w in warnings:
            if "Research failed" not in w:
                lines.append(f"⚠️ {w}")

        if total_mb > 500:
            lines.append(f"\n💡 **Tip:** Your workspace is using {total_mb:.0f} MB. "
                         "Consider clearing large arrays with `clear wave_sin` to free memory.")
        if lic:
            lines.append(f"🔑 **License:** {lic}")

    elif skill_name == "report_generator":
        path = data.get("report_path", "")
        fname = Path(path).name if path else "report.md"
        n_sections = len(data.get("sections") or [])
        lines.append(f"📄 **Report generated:** `{fname}`")
        lines.append(f"Contains {n_sections} section{'s' if n_sections != 1 else ''} with experiment history and lessons learned.")
        if data.get("sent"):
            lines.append("📤 Sent via Telegram.")
        else:
            lines.append(f"📁 Saved to: `{path}`")

    else:
        # Generic fallback: clean up the raw message
        msg = getattr(result, "message", "") or data.get("summary", "") or ""
        if msg:
            lines.append(msg)
        for w in (data.get("warnings") or []):
            lines.append(f"⚠️ {w}")
        if not lines:
            lines.append("✅ Skill completed successfully.")

    return "\n".join(lines)


def _run_trigger_skill_with_agent_activity(
    settings: MatClawSettings,
    skill_name: str,
    user_input: str,
    skill_kwargs: dict | None = None,
) -> None:
    """Run a skill with Research/Plan/Execute updates in sidebar."""
    from src.matclaw.matlab.matlab_bridge import MatlabBridge
    from src.matclaw.core.rpi_executor import RPIExecutor

    try:
        import matlab.engine as _me
        _sessions = _me.find_matlab()
        _sn = settings.matlab.session_name
        if _sn and _sn in _sessions:
            bridge = MatlabBridge(settings=settings.matlab)
        elif _sessions:
            from src.matclaw.config.base_config import MatlabSettings
            bridge = MatlabBridge(settings=MatlabSettings(session_name=_sessions[0], enabled=True))
        else:
            bridge = MatlabBridge(settings=settings.matlab)
    except Exception:
        bridge = MatlabBridge(settings=settings.matlab)
    bridge.start()
    mm = MemoryManager(persist_directory=".matclaw_chromadb")
    try:
        mm._ensure_client()
    except Exception:
        mm = None

    def _on_research(data: dict) -> None:
        st.session_state.agent_research = data

    def _on_plan(data: dict, sk: str, kwargs: dict) -> None:
        st.session_state.agent_plan = data
        st.session_state.agent_plan_skill = sk
        st.session_state.agent_plan_kwargs = kwargs or {}

    def _on_matlab_call(req) -> None:
        from src.matclaw.ui.components.agent_activity import format_matlab_call_as_code
        st.session_state.agent_execute_code = format_matlab_call_as_code(
            req.function, req.args, req.kwargs
        )

    bridge.set_before_call(_on_matlab_call)
    rpi = RPIExecutor(
        matlab_bridge=bridge,
        memory_manager=mm,
        on_research_complete=_on_research,
        on_plan_complete=_on_plan,
    )

    skill_kwargs = skill_kwargs or {}
    st.session_state.agent_status = "research"
    research_data = rpi.research(context=user_input)
    _on_research(research_data)
    st.session_state.agent_status = "plan"
    plan_data = rpi.plan(research_data, task_hint=user_input)
    _on_plan(plan_data, skill_name, skill_kwargs)
    st.session_state.agent_status = "execute"
    result = rpi.execute(plan_data, skill_name, skill_kwargs=skill_kwargs)
    data = getattr(result, "data", None) or {}

    if getattr(result, "success", False):
        msg = _format_skill_result_conversational(skill_name, result, data)
    else:
        err = getattr(result, "error", "") or "Skill failed."
        msg = f"❌ **{skill_name} failed:** {err}"

    st.session_state.agent_execute_code = data.get("code") or st.session_state.get("agent_execute_code", "")
    st.session_state._last_tool_result = msg
    st.session_state.agent_status = "complete"


def _handle_create_file(settings: MatClawSettings, prompt: str) -> str:
    """Create a .m file from user prompt and optionally run it."""
    import re

    # Extract filename: look for word.m pattern
    m = re.search(r'(\w+\.m)\b', prompt)
    if not m:
        return "❌ Could not determine filename. Use format: `create testing.m <code>`"

    filename = m.group(1)

    # Extract code: everything after the filename
    code_start = prompt.find(filename) + len(filename)
    code = prompt[code_start:].strip()

    if not code:
        return f"❌ No MATLAB code provided for `{filename}`. Paste the code after the filename."

    # Write the file to matlab/ directory
    matlab_dir = Path("matlab")
    matlab_dir.mkdir(exist_ok=True)
    file_path = matlab_dir / filename

    try:
        file_path.write_text(code, encoding="utf-8")
    except Exception as exc:
        return f"❌ Failed to write `{filename}`: {exc}"

    parts = [f"✅ Created `{file_path}`"]
    parts.append(f"```matlab\n{code[:2000]}\n```")

    # Try to run it
    try:
        from src.matclaw.matlab.matlab_bridge import MatlabBridge
        bridge = MatlabBridge(settings=settings.matlab)
        bridge.start()
        if bridge.is_healthy():
            bridge.addpath(str(matlab_dir.resolve()))
            success, output = bridge.run_matlab_code(f"run('{file_path.stem}')")
            if success:
                parts.append(f"▶️ **Output:**\n```\n{output[:1000]}\n```")
            else:
                parts.append(f"❌ **Runtime error:**\n```\n{output[:500]}\n```")
            bridge.stop()
        else:
            parts.append("⚠️ MATLAB bridge not available — file created but not run.")
    except Exception as exc:
        parts.append(f"⚠️ Could not run: {exc}")

    return "\n\n".join(parts)


def _run_analyze_file(settings: MatClawSettings, file_path: str, action: str = "fix_and_run") -> str:
    """Run the file analysis pipeline: read, analyze, fix, and/or run a .m file."""
    try:
        from src.matclaw.debug.debug_agent import DebugAgent
        from src.matclaw.matlab.matlab_bridge import MatlabBridge
        from src.matclaw.security.file_access import guard_file_access

        # Validate access
        decision = guard_file_access(file_path)
        if not decision.allow:
            return f"❌ Access denied: {decision.reason}"

        resolved = decision.resolved_path

        # Read file contents for display
        try:
            source_code = resolved.read_text(errors="replace")
        except Exception as exc:
            return f"❌ Cannot read file: {exc}"

        # Start MATLAB bridge — connect to shared session
        bridge = None
        try:
            import matlab.engine as _me
            _sessions = _me.find_matlab()
            _session_name = settings.matlab.session_name
            # If configured session is available, use it; else try first available
            if _session_name and _session_name in _sessions:
                bridge = MatlabBridge(settings=settings.matlab)
            elif _sessions:
                from src.matclaw.config.base_config import MatlabSettings
                bridge = MatlabBridge(settings=MatlabSettings(session_name=_sessions[0], enabled=True))
            else:
                bridge = MatlabBridge(settings=settings.matlab)
            bridge.start()
        except Exception as exc:
            return f"❌ MATLAB bridge failed to start: {exc}\n\n📄 **File:** `{resolved.name}`\n```matlab\n{source_code[:2000]}\n```"

        if not bridge.is_healthy():
            # Try to find and connect to any available shared session
            try:
                import matlab.engine as me
                sessions = me.find_matlab()
                if sessions:
                    from src.matclaw.config.base_config import MatlabSettings
                    retry_settings = MatlabSettings(session_name=sessions[0], enabled=True)
                    bridge = MatlabBridge(settings=retry_settings)
                    bridge.start()
            except Exception:
                pass

        if not bridge.is_healthy():
            parts = [f"📄 **File:** `{resolved.name}`"]
            parts.append(f"```matlab\n{source_code[:2000]}\n```")
            parts.append("❌ MATLAB bridge is not available. Make sure MATLAB is running with a shared engine:")
            parts.append("```matlab\nmatlab.engine.shareEngine('MatClawShared')\n```")
            return "\n\n".join(parts)

        mm = None
        try:
            mm = MemoryManager(persist_directory=".matclaw_chromadb")
            mm._ensure_client()
        except Exception:
            pass

        kb = None
        try:
            from src.matclaw.memory.knowledge_base import KnowledgeBase
            if mm is not None:
                kb = KnowledgeBase(mm)
        except Exception:
            pass

        debug_agent = DebugAgent(
            bridge,
            matlab_root=resolved.parent,
            memory_manager=mm,
            knowledge_base=kb,
        )
        result = debug_agent.analyze_and_fix_file(resolved, action=action)

        # Release bridge (don't quit shared session)
        try:
            bridge.stop()
        except Exception:
            pass

        # Format result
        parts = [f"📄 **File:** `{resolved.name}`"]
        parts.append(f"```matlab\n{source_code[:2000]}\n```")

        if result.fixed:
            parts.append("✅ **Result:** Fixed and ran successfully!")
        elif result.fix_error:
            parts.append(f"❌ **Error:** {result.fix_error}")

        if result.suggested_changes:
            parts.append(f"💡 **Analysis:** {result.suggested_changes}")

        if result.applied_to_source:
            parts.append(f"📝 Applied fix to source (backup at `{resolved.name}.bak`)")

        return "\n\n".join(parts)

    except Exception as exc:
        return f"❌ File analysis failed: {exc}"


def _run_query_memory(settings: MatClawSettings, query: str) -> str:
    """Query memory and return formatted results."""
    try:
        mm = MemoryManager(persist_directory=".matclaw_chromadb")
        mm._ensure_client()
        results = mm.query_context(query, n_results=5)
        if not results:
            return "No matching artifacts in memory."
        lines = []
        for i, r in enumerate(results, 1):
            meta = r.get("metadata") or {}
            doc = r.get("document") or ""
            summary = meta.get("summary") or meta.get("message") or doc[:200]
            lines.append(f"**{i}.** {summary}")
        return "\n\n".join(lines)
    except Exception as exc:
        return f"Memory query failed: {exc}"


def _get_last_tool_result() -> str:
    """Get the last tool execution result from session state."""
    return getattr(st.session_state, "_last_tool_result", "") or ""


def _render_interactive_tuner_tab(settings: MatClawSettings) -> None:
    """Render the Interactive Tuner tab: requires MATLAB bridge and optional RPIExecutor."""
    from src.matclaw.core.rpi_executor import RPIExecutor
    from src.matclaw.matlab.matlab_bridge import MatlabBridge, MatlabCallRequest

    matlab_bridge = MatlabBridge(settings=settings.matlab)

    def _on_matlab_call(req: MatlabCallRequest) -> None:
        from src.matclaw.ui.components.agent_activity import format_matlab_call_as_code
        st.session_state.agent_execute_code = format_matlab_call_as_code(
            req.function, req.args, req.kwargs
        )

    matlab_bridge.set_before_call(_on_matlab_call)

    try:
        matlab_bridge.start()
    except Exception as exc:
        st.error(f"MATLAB bridge failed to start: {exc}")
        st.info("Ensure MATLAB Engine for Python is installed and a shared session is available.")
        return

    if not matlab_bridge.is_healthy():
        st.warning(
            "MATLAB bridge is not healthy. The tuner may not work. "
            "Check the Session Manager tab for connection status."
        )

    memory_manager = MemoryManager(persist_directory=".matclaw_chromadb")
    try:
        memory_manager._ensure_client()
    except Exception:
        memory_manager = None

    def _on_research(data: dict) -> None:
        st.session_state.agent_research = data

    def _on_plan(data: dict, skill_name: str, skill_kwargs: dict) -> None:
        st.session_state.agent_plan = data
        st.session_state.agent_plan_skill = skill_name
        st.session_state.agent_plan_kwargs = skill_kwargs

    rpi_executor = RPIExecutor(
        matlab_bridge=matlab_bridge,
        memory_manager=memory_manager,
        on_run_complete=None,
        hitl_threshold_seconds=9999,
        on_pending_hitl=None,
        on_research_complete=_on_research,
        on_plan_complete=_on_plan,
    )

    if matlab_bridge.is_healthy() and st.session_state.agent_research is None:
        research_data = rpi_executor.research()
        _on_research(research_data)

    render_interactive_tuner(matlab_bridge=matlab_bridge, rpi_executor=rpi_executor)


if __name__ == "__main__":
    main()
