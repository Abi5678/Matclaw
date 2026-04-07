"""
Baseline checks for production-related settings (offline).

For a future live golden eval (agentic + MATLAB), use a scheduled/self-hosted job with
`MATLAB` available, LLM keys set, and env `MATCLAW_EVAL_LIVE=1`, then add cases beside the
mocked tests in `tests/core/test_agentic_loop_limits.py`. Ops hooks: `/metrics`, `/health`.
"""

from src.matclaw.config.base_config import MatClawSettings


def test_matclaw_settings_includes_production_block():
    s = MatClawSettings()
    assert s.production.memory_n_results >= 1
    assert s.production.max_agentic_wall_seconds >= 30
    assert isinstance(s.production.soft_cost_cap_usd_per_task, float)
    assert s.production.store_agentic_episodes is True
    assert s.production.agentic_max_concurrent >= 1
    assert s.production.usd_per_1k_prompt_tokens >= 0.0
