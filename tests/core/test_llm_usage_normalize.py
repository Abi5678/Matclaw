"""Provider usage normalization for metering."""

from types import SimpleNamespace

from src.matclaw.llm.llm_client import _usage_to_dict


def test_usage_openai_style_object():
    u = SimpleNamespace(prompt_tokens=10, completion_tokens=3, total_tokens=13)
    d = _usage_to_dict(u)
    assert d == {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}


def test_usage_anthropic_style_object():
    u = SimpleNamespace(input_tokens=7, output_tokens=2)
    d = _usage_to_dict(u)
    assert d == {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}


def test_usage_dict():
    d = _usage_to_dict({"prompt_tokens": 1, "completion_tokens": 4, "total_tokens": 5})
    assert d == {"prompt_tokens": 1, "completion_tokens": 4, "total_tokens": 5}


def test_usage_empty():
    assert _usage_to_dict(None) is None
    assert _usage_to_dict(SimpleNamespace()) is None
