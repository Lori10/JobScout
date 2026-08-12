"""Provider abstraction so ai_ranker.py can call any LLM backend the same
way. Each provider normalizes its own SDK's errors into
TransientProviderError (retryable: rate limit, timeout, 5xx) or
FatalProviderError (not retryable: bad/missing key, invalid request), so
AIRanker's retry loop stays provider-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol

from pydantic import BaseModel


class ProviderError(Exception):
    """Base for all provider errors."""


class TransientProviderError(ProviderError):
    """Retryable: rate limit, timeout, 5xx, malformed-but-retriable output."""


class FatalProviderError(ProviderError):
    """Not retryable: bad/missing key, permission denied, invalid request."""


@dataclass
class LLMResult:
    parsed: BaseModel
    input_tokens: int = 0
    output_tokens: int = 0


class LLMProvider(Protocol):
    api_key_env: ClassVar[str]

    def __init__(self, api_key: str, model: str) -> None: ...

    def complete_structured(
        self, *, system: str, user_message: str, schema: type[BaseModel], max_tokens: int
    ) -> LLMResult: ...
