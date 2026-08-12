"""Google Gemini provider — the default free-tier backend (config.yaml's
ai.provider). Get a free key at https://aistudio.google.com/apikey and set
it as GEMINI_API_KEY.
"""

from __future__ import annotations

import json

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import BaseModel

from jobscout.llm.base import FatalProviderError, LLMResult, TransientProviderError

# 401/403 are the only errors we treat as not-worth-retrying; anything else
# (429, 5xx, and anything unrecognized) is assumed transient so a genuinely
# transient failure never silently skips AIRanker's retry loop.
_FATAL_STATUS_CODES = {401, 403}


class GeminiProvider:
    api_key_env = "GEMINI_API_KEY"

    def __init__(self, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def complete_structured(
        self, *, system: str, user_message: str, schema: type[BaseModel], max_tokens: int
    ) -> LLMResult:
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=user_message,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=schema,
                    max_output_tokens=max_tokens,
                ),
            )
        except genai_errors.APIError as exc:
            status_code = getattr(exc, "code", None)
            if status_code in _FATAL_STATUS_CODES:
                raise FatalProviderError(str(exc)) from exc
            raise TransientProviderError(str(exc)) from exc
        except Exception as exc:  # unexpected shape from the SDK/transport
            raise TransientProviderError(str(exc)) from exc

        parsed = response.parsed
        if parsed is None:
            # SDK versions occasionally leave .parsed unset even with a
            # response_schema; fall back to parsing .text ourselves before
            # giving up, so a minor SDK behavior difference doesn't crash.
            if not response.text:
                raise TransientProviderError("Gemini returned no content")
            try:
                parsed = schema.model_validate(json.loads(response.text))
            except Exception as exc:
                raise TransientProviderError(f"Gemini's response failed schema validation: {exc}") from exc

        usage = response.usage_metadata
        return LLMResult(
            parsed=parsed,
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(usage.candidates_token_count or 0) if usage else 0,
        )
