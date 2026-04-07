"""Tests for MATLAB script / local-function splitting used by batch + engine wrappers."""

from src.matclaw.matlab.batch_runner import split_matlab_script_and_local_functions


def test_split_no_functions():
    body, loc = split_matlab_script_and_local_functions("x = 1;\ny = 2;\n")
    assert "x = 1" in body
    assert loc == ""


def test_split_with_local_function():
    src = """a = 1;
b = 2;

function y = foo(x)
y = x + 1;
end
"""
    body, loc = split_matlab_script_and_local_functions(src)
    assert "a = 1" in body
    assert "function y = foo" in loc
    assert "foo" not in body


def test_comment_line_not_split():
    src = "% function pretend\nz = 3;\n"
    body, loc = split_matlab_script_and_local_functions(src)
    assert loc == ""
    assert "pretend" in body
