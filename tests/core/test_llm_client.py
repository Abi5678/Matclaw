"""Tests for llm_client utility functions (no live API calls)."""
import pytest

from matclaw.llm.llm_client import (
    _strip_embedded_json_array,
    _usage_to_dict,
    resolve_api_key,
)


# ── _strip_embedded_json_array ────────────────────────────────────────────────

def test_strip_no_json_array():
    assert _strip_embedded_json_array("plain text") == "plain text"


def test_strip_simple_array():
    result = _strip_embedded_json_array('prefix [{"key": "val"}] suffix')
    assert "[{" not in result
    assert "prefix" in result
    assert "suffix" in result


def test_strip_empty_string():
    assert _strip_embedded_json_array("") == ""


def test_strip_nested_array():
    # Nested brackets — depth tracking must handle them correctly
    text = 'before [{"a": [1,2]}] after'
    result = _strip_embedded_json_array(text)
    assert "[{" not in result


def test_strip_unclosed_array():
    # Unclosed array should return original text unchanged
    text = "hello [{ no closing"
    assert _strip_embedded_json_array(text) == text


# ── _usage_to_dict ────────────────────────────────────────────────────────────

def test_usage_to_dict_none():
    assert _usage_to_dict(None) is None


def test_usage_to_dict_with_object():
    class FakeUsage:
        prompt_tokens = 10
        completion_tokens = 20
        total_tokens = 30

    result = _usage_to_dict(FakeUsage())
    assert result is not None
    assert result.get("prompt_tokens") == 10
    assert result.get("completion_tokens") == 20


# ── resolve_api_key ───────────────────────────────────────────────────────────

def test_resolve_api_key_explicit():
    key = resolve_api_key("anthropic", "explicit-key")
    assert key == "explicit-key"


def test_resolve_api_key_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    key = resolve_api_key("anthropic", None)
    assert key == "env-key"


def test_resolve_api_key_google(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    key = resolve_api_key("google", None)
    assert key == "google-key"


def test_resolve_api_key_nvidia(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-key")
    key = resolve_api_key("nvidia", None)
    assert key == "nvidia-key"


def test_resolve_api_key_missing_returns_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    key = resolve_api_key("anthropic", None)
    assert key is None


# ── call_chat_completion (mocked) ─────────────────────────────────────────────

def test_call_chat_completion_missing_key_raises():
    from matclaw.llm.llm_client import call_chat_completion
    import os
    # Ensure no key in env
    old = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        with pytest.raises(RuntimeError, match="Missing API key"):
            call_chat_completion(
                provider="anthropic",
                model="claude-haiku-4-5-20251001",
                system="sys",
                messages=[{"role": "user", "content": "hi"}],
                api_key=None,
            )
    finally:
        if old:
            os.environ["ANTHROPIC_API_KEY"] = old


def test_call_chat_completion_anthropic_mocked(monkeypatch):
    """call_chat_completion returns text from the Anthropic provider (mocked)."""
    from unittest.mock import MagicMock, patch

    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text="hello from mock")]

    with patch("matclaw.llm.llm_client.Anthropic") as MockAnthropic:
        instance = MockAnthropic.return_value
        instance.messages.create.return_value = mock_resp

        from matclaw.llm.llm_client import call_chat_completion
        result = call_chat_completion(
            provider="anthropic",
            model="claude-haiku-4-5-20251001",
            system="You are helpful.",
            messages=[{"role": "user", "content": "hello"}],
            api_key="fake-key",
        )

    assert result == "hello from mock"
