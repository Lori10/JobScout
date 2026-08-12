"""Provider registry — the extension point for new LLM backends, mirroring
jobscout/fetchers/__init__.py's FETCHER_REGISTRY pattern. Adding a provider
is a new file here plus one registry entry; ai_ranker.py and pipeline.py
never need to change.
"""

from jobscout.llm.anthropic_provider import AnthropicProvider
from jobscout.llm.base import (
    FatalProviderError,
    LLMProvider,
    LLMResult,
    ProviderError,
    TransientProviderError,
)
from jobscout.llm.gemini import GeminiProvider

PROVIDER_REGISTRY: dict[str, type] = {
    "gemini": GeminiProvider,
    "anthropic": AnthropicProvider,
}


def build_provider(name: str, api_key: str, model: str) -> LLMProvider:
    try:
        provider_cls = PROVIDER_REGISTRY[name]
    except KeyError:
        raise ValueError(f"unknown ai.provider {name!r}, expected one of {sorted(PROVIDER_REGISTRY)}") from None
    return provider_cls(api_key, model)


__all__ = [
    "PROVIDER_REGISTRY",
    "build_provider",
    "LLMProvider",
    "LLMResult",
    "ProviderError",
    "TransientProviderError",
    "FatalProviderError",
    "GeminiProvider",
    "AnthropicProvider",
]
