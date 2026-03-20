"""
Hybrid RPI control surface: Voice Intent + Code Snippet → same prompt as KeystrokeManager (Ctrl+Alt+M).

Mirrors the gateway pattern for users without Telegram or global hotkeys.
"""

from __future__ import annotations

import logging
import streamlit as st

from src.matclaw.config.base_config import MatClawSettings
from src.matclaw.core.rpi_executor import RPIExecutor
from src.matclaw.gateways.keystroke_manager import (
    _DEFAULT_INTENT,
    HybridTriggerPayload,
    assemble_hybrid_payload,
)
from src.matclaw.matlab.matlab_bridge import MatlabBridge
from src.matclaw.memory.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

try:
    import pyperclip
except ImportError:  # pragma: no cover
    pyperclip = None  # type: ignore[assignment]


def render_hybrid_rpi_panel(settings: MatClawSettings) -> None:
    """Streamlit: two-part hybrid context + run ``RPIExecutor.run_flow``."""
    st.subheader("Hybrid context → RPI")
    st.caption(
        "Combine **voice / intent** (what you want) with a **code snippet** (from the editor or clipboard). "
        f"Empty intent defaults to “{_DEFAULT_INTENT}”; empty code defaults to “(empty clipboard)”—same rules as the **Ctrl+Alt+M** gateway."
    )

    st.session_state.setdefault("hybrid_last_result", None)

    col_voice, col_code = st.columns(2)
    with col_voice:
        st.text_area(
            "Voice intent",
            height=140,
            key="hybrid_voice",
            placeholder="e.g. Tune this PID for less overshoot",
            help=f"If left empty at run time, “{_DEFAULT_INTENT}” is used.",
        )
    with col_code:
        st.text_area(
            "Code snippet (target)",
            height=140,
            key="hybrid_code",
            placeholder="Paste MATLAB, or use Import from clipboard",
            help="Becomes the “Code Snippet” line in the hybrid prompt.",
        )

    voice = str(st.session_state.get("hybrid_voice") or "")
    code = str(st.session_state.get("hybrid_code") or "")

    clip_ok = pyperclip is not None
    btn_col1, btn_col2, _ = st.columns([1, 1, 2])
    with btn_col1:
        if st.button("Import from clipboard", disabled=not clip_ok, help="Reads system clipboard into Code snippet"):
            if pyperclip is not None:
                try:
                    pasted = pyperclip.paste() or ""
                    st.session_state.hybrid_code = pasted
                    st.rerun()
                except Exception as exc:
                    st.error(f"Clipboard read failed: {exc}")
    with btn_col2:
        if st.button("Clear fields"):
            st.session_state.hybrid_voice = ""
            st.session_state.hybrid_code = ""
            st.session_state.hybrid_last_result = None
            st.rerun()

    if not clip_ok:
        st.warning("Install **pyperclip** to enable clipboard import (`pip install pyperclip`).")

    payload = assemble_hybrid_payload(voice, code)
    with st.expander("Hybrid prompt preview (sent to `run_flow`)", expanded=True):
        st.code(payload.hybrid_prompt, language="text")

    st.divider()
    if st.button("Run RPI flow", type="primary", help="Invokes RPIExecutor.run_flow(hybrid_prompt)"):
        _run_hybrid_flow(settings, payload)

    last = st.session_state.hybrid_last_result
    if last is not None:
        if isinstance(last, dict) and last.get("error"):
            st.error("Last run failed.")
        else:
            st.success("Last run completed.")
        _render_flow_result(last)

    with st.expander("Global hotkey (optional)", expanded=False):
        st.markdown(
            """
When the **daemon** runs with `MATCLAW_KEYSTROKE_GATEWAY=1`, **Ctrl+Alt+M** builds the same hybrid
string from `BufferedVoiceClient` + system clipboard and calls `run_flow` in the background.
Streamlit here is the **manual** equivalent—no Telegram required.
            """.strip()
        )


def _run_hybrid_flow(settings: MatClawSettings, payload: HybridTriggerPayload) -> None:
    bridge: MatlabBridge | None = None
    try:
        with st.spinner("Running Research → Plan → Implement (`run_flow`)…"):
            bridge = MatlabBridge(settings=settings.matlab)
            bridge.start()
            if not bridge.is_healthy():
                st.session_state.hybrid_last_result = {
                    "error": "MATLAB bridge not healthy. Install Engine for Python and check Session Manager.",
                }
                st.error(st.session_state.hybrid_last_result["error"])
                return

            mm = MemoryManager(persist_directory=".matclaw_chromadb")
            try:
                mm._ensure_client()
            except Exception:
                mm = None  # type: ignore[assignment]

            rpi = RPIExecutor(
                matlab_bridge=bridge,
                memory_manager=mm,
                on_run_complete=None,
                hitl_threshold_seconds=9999,
                on_pending_hitl=None,
            )
            st.session_state.agent_status = "research"
            st.session_state.agent_research = {
                "hybrid_context": True,
                "voice_resolved": payload.voice_intent,
                "preview": payload.hybrid_prompt[:1200],
            }
            st.session_state.agent_plan = {"mode": "run_flow", "hybrid_prompt": payload.hybrid_prompt[:500]}
            st.session_state.agent_plan_skill = "run_flow"
            st.session_state.agent_plan_kwargs = {}
            st.session_state.agent_execute_code = payload.clipboard_text[:2000] if payload.clipboard_text else ""
            st.session_state.agent_status = "execute"

            result = rpi.run_flow(payload.hybrid_prompt, settings=settings)
            st.session_state.hybrid_last_result = result
            st.session_state.agent_status = "complete"
            logger.info("Hybrid RPI UI run_flow finished: %s", str(result)[:500])
    except Exception as exc:
        logger.exception("Hybrid RPI UI run failed")
        st.session_state.hybrid_last_result = {"error": str(exc)}
        st.error(str(exc))
        st.session_state.agent_status = "idle"
    finally:
        if bridge is not None:
            try:
                bridge.stop()
            except Exception:
                logger.exception("Hybrid RPI UI: bridge stop failed")


def _render_flow_result(result: object) -> None:
    if isinstance(result, dict) and result.get("pending"):
        st.warning("Run is pending human approval (HITL). Complete approval via your configured channel.")
        st.json(result)
        return
    if isinstance(result, dict) and result.get("error"):
        st.error(result["error"])
        return
    text = repr(result) if not isinstance(result, str) else result
    st.text_area("Result", value=text[:20000], height=320, disabled=True)

