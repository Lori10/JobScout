"""Anthropic (Claude) provider — the drop-in alternative to the default free
Gemini provider. Set config.yaml's ai.provider: anthropic and export
ANTHROPIC_API_KEY to switch.

Uses forced tool-use (tool_choice pinned to a single tool) rather than a
free-text prompt for structured output: this is a single-shot extraction,
not an agentic tool loop, and forced tool-use is a long-stable way to get
reliably parseable JSON out of the Messages API.
"""

from __future__ import annotations

import anthropic
from pydantic import BaseModel, ValidationError

from jobscout.llm.base import FatalProviderError, LLMResult, TransientProviderError

_TOOL_NAME = "submit_ranking"


class AnthropicProvider:
    api_key_env = "ANTHROPIC_API_KEY"

    def __init__(self, api_key: str, model: str) -> None:
        # max_retries=0: AIRanker owns a single retry loop shared across
        # every provider, since retry semantics/exception types differ per SDK.
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=0)
        self._model = model

    def complete_structured(
        self, *, system: str, user_message: str, schema: type[BaseModel], max_tokens: int
    ) -> LLMResult:
        tool = {
            "name": _TOOL_NAME,
            "description": "Submit the structured ranking result for this job.",
            "input_schema": schema.model_json_schema(),
        }
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_message}],
                tools=[tool],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise FatalProviderError(str(exc)) from exc
        except anthropic.APIError as exc:
            raise TransientProviderError(str(exc)) from exc

        tool_use_block = next(
            (block for block in response.content if getattr(block, "type", None) == "tool_use"),
            None,
        )
        if tool_use_block is None:
            raise TransientProviderError("Claude did not return the expected tool_use block")
        try:
            parsed = schema.model_validate(tool_use_block.input)
        except ValidationError as exc:
            raise TransientProviderError(f"Claude's structured output failed schema validation: {exc}") from exc

        usage = response.usage
        return LLMResult(
            parsed=parsed,
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
        )
