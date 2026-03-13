"""Interactive Tuner: Kp/Ki/Kd sliders, Run Optimization, and step response plot."""

from __future__ import annotations

import streamlit as st

try:
    import plotly.graph_objects as go
    import numpy as np
except ImportError:
    go = None
    np = None


def render_step_response_plot(
    t: "np.ndarray",
    y: "np.ndarray",
    target_value: float = 1.0,
    title: str = "Step Response: Target vs Actual",
) -> None:
    """Plot step response with Target (reference) and Actual (system output) overlaid."""
    if go is None or np is None:
        st.warning("plotly and numpy required for step response plot.")
        return
    if t is None or y is None or len(t) == 0 or len(y) == 0:
        st.info("No step response data to plot. Run a simulation first.")
        return

    t_flat = np.asarray(t).flatten()
    y_flat = np.asarray(y).flatten()
    if len(t_flat) != len(y_flat):
        y_flat = y_flat[: len(t_flat)] if len(y_flat) > len(t_flat) else np.pad(y_flat, (0, len(t_flat) - len(y_flat)))

    # Target: step reference at target_value from t=0
    t_target = np.array([max(0, t_flat[0] - 0.01), 0.0, t_flat[-1] + 0.01])
    y_target = np.array([0.0, target_value, target_value])

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=t_target,
            y=y_target,
            mode="lines",
            name="Target",
            line=dict(color="rgba(59, 130, 246, 0.7)", width=2, dash="dash"),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=t_flat,
            y=y_flat,
            mode="lines",
            name="Actual",
            line=dict(color="#16a34a", width=2),
        )
    )
    fig.update_layout(
        title=title,
        xaxis_title="Time (s)",
        yaxis_title="Output",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=50, r=20, t=50, b=50),
    )
    try:
        from src.matclaw.ui.components.visualization_panel import update_latest_plot
        update_latest_plot(plotly_fig=fig)
    except Exception:
        pass
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": True})


def render_interactive_tuner(
    matlab_bridge: object,
    rpi_executor: object | None,
) -> None:
    """
    Render the Interactive Tuner: sliders, Run Simulation, Run Optimization, and live plot.
    matlab_bridge: MatlabBridge instance (must be started).
    rpi_executor: RPIExecutor instance for Run Optimization (optional).
    """
    st.subheader("Interactive PID Tuner")

    col1, col2 = st.columns([1, 2])
    with col1:
        kp = st.slider("Kp", min_value=0.01, max_value=20.0, value=1.0, step=0.05, key="tuner_kp")
        ki = st.slider("Ki", min_value=0.0, max_value=10.0, value=0.5, step=0.05, key="tuner_ki")
        kd = st.slider("Kd", min_value=0.0, max_value=10.0, value=0.0, step=0.05, key="tuner_kd")

        run_sim = st.button("Run Simulation", key="tuner_run_sim")
        run_opt = st.button("Run Optimization", key="tuner_run_opt")

    with col2:
        if "tuner_t" not in st.session_state:
            st.session_state.tuner_t = None
        if "tuner_y" not in st.session_state:
            st.session_state.tuner_y = None
        if "tuner_metrics" not in st.session_state:
            st.session_state.tuner_metrics = None
        if "tuner_message" not in st.session_state:
            st.session_state.tuner_message = None

        if run_sim:
            with st.spinner("Running simulation..."):
                try:
                    from src.matclaw.skills.pid_optimizer.logic import run_single_eval

                    r, t, y = run_single_eval(matlab_bridge, Kp=kp, Ki=ki, Kd=kd)
                    st.session_state.tuner_t = t
                    st.session_state.tuner_y = y
                    st.session_state.tuner_metrics = r.get("metrics")
                    st.session_state.tuner_message = r.get("error") or (
                        f"Rise time: {r.get('metrics', {}).get('rise_time', 'N/A')} s, "
                        f"Overshoot: {r.get('metrics', {}).get('overshoot_pct', 'N/A')}%"
                    )
                except Exception as exc:
                    st.error(str(exc))
                    st.session_state.tuner_message = str(exc)

        if run_opt and rpi_executor:
            with st.spinner("Running PID optimization (may take a minute)..."):
                try:
                    from src.matclaw.skills.pid_optimizer.logic import run_single_eval

                    result = rpi_executor.run_rpi(
                        "pid_optimizer",
                        Kp=kp,
                        Ki=ki,
                        Kd=kd,
                        target_rise_time=1.0,
                        target_overshoot_pct=15.0,
                        max_iterations=10,
                    )
                    if getattr(result, "success", False) and getattr(result, "data", None):
                        data = result.data
                        gains = data.get("gains", {})
                        final_kp = gains.get("Kp", kp)
                        final_ki = gains.get("Ki", ki)
                        final_kd = gains.get("Kd", kd)
                        r, t, y = run_single_eval(
                            matlab_bridge,
                            Kp=final_kp,
                            Ki=final_ki,
                            Kd=final_kd,
                        )
                        st.session_state.tuner_t = t
                        st.session_state.tuner_y = y
                        st.session_state.tuner_metrics = r.get("metrics")
                        st.session_state.tuner_message = getattr(result, "message", "Optimization complete.")
                    else:
                        st.session_state.tuner_message = getattr(result, "error", "Optimization failed.")
                except Exception as exc:
                    st.error(str(exc))
                    st.session_state.tuner_message = str(exc)

        if st.session_state.tuner_message:
            st.success(st.session_state.tuner_message)

        render_step_response_plot(
            st.session_state.tuner_t,
            st.session_state.tuner_y,
            target_value=1.0,
            title="Step Response: Target vs Actual",
        )
