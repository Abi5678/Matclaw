import pytest
import re as _re
from src.matclaw.api.server import _sanitize_matlab_code

@pytest.mark.parametrize("code_snippet, expected", [
    ("'FaceColor', 'gray'", "'FaceColor', [0.5 0.5 0.5]"),
    ("'Color', 'gray'", "'Color', [0.5 0.5 0.5]"),
    ("'edgecolor','gray'", "'edgecolor', [0.5 0.5 0.5]"),
    ("'MarkerFaceColor', 'gray'", "'MarkerFaceColor', [0.5 0.5 0.5]"),
    ("'MarkerEdgeColor', 'gray'", "'MarkerEdgeColor', [0.5 0.5 0.5]"),
    ("surf(X,Y,Z,'FaceColor','gray')", "surf(X,Y,Z,'FaceColor', [0.5 0.5 0.5])"),
])
def test_sanitize_matlab_code(code_snippet, expected):
    """Verify that 'gray' color property is correctly converted to RGB."""
    global _sanitize_matlab_code
    # Use the same function as the server
    assert _sanitize_matlab_code(code_snippet) == expected
