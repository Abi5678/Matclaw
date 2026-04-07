"""Spec vs run-success signals for agentic MATLAB quality."""

from src.matclaw.core.spec_compliance import (
    SpecComplianceResult,
    analyze_matlab_run,
    user_requests_base_matlab_only,
)


def test_user_requests_base_matlab():
    assert user_requests_base_matlab_only("Use only base MATLAB — no toolboxes.")
    assert not user_requests_base_matlab_only("Plot sin(x).")


def test_knnsearch_violates_base_only():
    code = "d = knnsearch(X, y);"
    r = analyze_matlab_run(
        code=code,
        user_text="base matlab only",
        tool_output_success=True,
        plots=["/plots/p.png"],
    )
    assert r.run_success is True
    assert r.spec_satisfied is False
    assert any("knnsearch" in v for v in r.violations)


def test_clean_base_plot_ok():
    r = analyze_matlab_run(
        code="figure; plot(1:10);",
        user_text="base matlab only, show a plot",
        tool_output_success=True,
        plots=["/plots/a.png"],
    )
    assert r.spec_satisfied is True


def test_plot_expected_but_missing():
    r = analyze_matlab_run(
        code="x=1;",
        user_text="plot the results",
        tool_output_success=True,
        plots=[],
    )
    assert r.spec_satisfied is False


def test_to_dict():
    r = SpecComplianceResult(run_success=True, spec_satisfied=True, user_asked_base_only=False)
    assert r.to_dict()["run_success"] is True
