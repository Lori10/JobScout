"""Provider error-normalization tests. Each provider is constructed without
calling its real __init__ (so no real SDK client / network setup happens)
and its internal client is replaced with a small fake exposing just the
methods AnthropicProvider/GeminiProvider actually call.
"""

import anthropic
import httpx
import pytest
from google.genai import errors as genai_errors
from pydantic import BaseModel

from jobscout.llm.anthropic_provider import AnthropicProvider
from jobscout.llm.base import FatalProviderError, TransientProviderError
from jobscout.llm.gemini import GeminiProvider


class _Schema(BaseModel):
    value: str


# ---- Gemini ----------------------------------------------------------


class _FakeUsageMetadata:
    def __init__(self, prompt_tokens, candidate_tokens):
        self.prompt_token_count = prompt_tokens
        self.candidates_token_count = candidate_tokens


class _FakeGeminiResponse:
    def __init__(self, parsed=None, text="", usage_metadata=None):
        self.parsed = parsed
        self.text = text
        self.usage_metadata = usage_metadata


class _FakeModels:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    def generate_content(self, **kwargs):
        if self._exc is not None:
            raise self._exc
        return self._response


def _gemini_provider(response=None, exc=None) -> GeminiProvider:
    provider = GeminiProvider.__new__(GeminiProvider)
    provider._model = "gemini-2.5-flash"
    provider._client = type("FakeClient", (), {})()
    provider._client.models = _FakeModels(response, exc)
    return provider


def test_gemini_success_returns_parsed_and_token_counts():
    parsed = _Schema(value="ok")
    provider = _gemini_provider(response=_FakeGeminiResponse(parsed=parsed, usage_metadata=_FakeUsageMetadata(10, 20)))
    result = provider.complete_structured(system="sys", user_message="hi", schema=_Schema, max_tokens=100)
    assert result.parsed == parsed
    assert result.input_tokens == 10
    assert result.output_tokens == 20


def test_gemini_falls_back_to_manual_json_parse_when_parsed_is_none():
    provider = _gemini_provider(response=_FakeGeminiResponse(parsed=None, text='{"value": "ok"}'))
    result = provider.complete_structured(system="sys", user_message="hi", schema=_Schema, max_tokens=100)
    assert result.parsed == _Schema(value="ok")


def test_gemini_401_is_fatal():
    exc = genai_errors.ClientError(401, {"error": {"message": "bad key"}})
    provider = _gemini_provider(exc=exc)
    with pytest.raises(FatalProviderError):
        provider.complete_structured(system="sys", user_message="hi", schema=_Schema, max_tokens=100)


def test_gemini_503_is_transient():
    exc = genai_errors.ServerError(503, {"error": {"message": "overloaded"}})
    provider = _gemini_provider(exc=exc)
    with pytest.raises(TransientProviderError):
        provider.complete_structured(system="sys", user_message="hi", schema=_Schema, max_tokens=100)


def test_gemini_unexpected_exception_is_transient_not_fatal():
    provider = _gemini_provider(exc=RuntimeError("weird transport error"))
    with pytest.raises(TransientProviderError):
        provider.complete_structured(system="sys", user_message="hi", schema=_Schema, max_tokens=100)


# ---- Anthropic ---------------------------------------------------------


class _FakeToolUseBlock:
    def __init__(self, input_data):
        self.type = "tool_use"
        self.input = input_data


class _FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeAnthropicResponse:
    def __init__(self, content, usage=None):
        self.content = content
        self.usage = usage


class _FakeMessages:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    def create(self, **kwargs):
        if self._exc is not None:
            raise self._exc
        return self._response


def _anthropic_provider(response=None, exc=None) -> AnthropicProvider:
    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider._model = "claude-haiku-4-5"
    provider._client = type("FakeClient", (), {})()
    provider._client.messages = _FakeMessages(response, exc)
    return provider


def _http_response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx.Response(status_code, request=request)


def test_anthropic_success_returns_parsed_and_token_counts():
    response = _FakeAnthropicResponse(
        content=[_FakeToolUseBlock({"value": "ok"})],
        usage=_FakeUsage(5, 15),
    )
    provider = _anthropic_provider(response=response)
    result = provider.complete_structured(system="sys", user_message="hi", schema=_Schema, max_tokens=100)
    assert result.parsed == _Schema(value="ok")
    assert result.input_tokens == 5
    assert result.output_tokens == 15


def test_anthropic_missing_tool_use_block_is_transient():
    response = _FakeAnthropicResponse(content=[], usage=_FakeUsage(1, 1))
    provider = _anthropic_provider(response=response)
    with pytest.raises(TransientProviderError):
        provider.complete_structured(system="sys", user_message="hi", schema=_Schema, max_tokens=100)


def test_anthropic_schema_validation_failure_is_transient():
    response = _FakeAnthropicResponse(content=[_FakeToolUseBlock({"unexpected": 1})], usage=_FakeUsage(1, 1))
    provider = _anthropic_provider(response=response)
    with pytest.raises(TransientProviderError):
        provider.complete_structured(system="sys", user_message="hi", schema=_Schema, max_tokens=100)


def test_anthropic_authentication_error_is_fatal():
    exc = anthropic.AuthenticationError("bad key", response=_http_response(401), body=None)
    provider = _anthropic_provider(exc=exc)
    with pytest.raises(FatalProviderError):
        provider.complete_structured(system="sys", user_message="hi", schema=_Schema, max_tokens=100)


def test_anthropic_permission_denied_error_is_fatal():
    exc = anthropic.PermissionDeniedError("forbidden", response=_http_response(403), body=None)
    provider = _anthropic_provider(exc=exc)
    with pytest.raises(FatalProviderError):
        provider.complete_structured(system="sys", user_message="hi", schema=_Schema, max_tokens=100)


def test_anthropic_rate_limit_error_is_transient():
    exc = anthropic.RateLimitError("slow down", response=_http_response(429), body=None)
    provider = _anthropic_provider(exc=exc)
    with pytest.raises(TransientProviderError):
        provider.complete_structured(system="sys", user_message="hi", schema=_Schema, max_tokens=100)
