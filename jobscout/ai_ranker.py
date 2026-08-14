"""AI scorer (Phase 3). Produces the exact same RankResult shape ranker.py's
heuristic scorer produces (plus the additive seniority_fit field), via
whichever jobscout/llm/ provider config.yaml's ai.provider selects — pipeline.py
and report.py stay scorer-agnostic either way.

The "is this actually a job posting?" spam/sanity check (see PLAN.md) is
folded into the same structured-output call as scoring, rather than a
second API call, to avoid doubling cost per job.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Literal

from pydantic import BaseModel

from jobscout.config import AIConfig, Profile
from jobscout.llm.base import FatalProviderError, LLMProvider, TransientProviderError
from jobscout.models import ContractTypeGuess, Job, RankingSource, SeniorityFit
from jobscout.ranker import RankResult, eligibility_confidence_for

logger = logging.getLogger(__name__)

_MAX_OUTPUT_TOKENS = 2048

# Approximate $ per million tokens (input, output) — check the provider's
# current pricing page before relying on this for real budgeting; it will
# drift. Gemini's free-tier key (the default provider) is $0.
_PRICING: dict[tuple[str, str], tuple[float, float]] = {
    ("gemini", "gemini-3.5-flash-lite"): (0.0, 0.0),
    ("gemini", "gemini-3.5-flash"): (0.0, 0.0),
    ("gemini", "gemini-2.5-flash"): (0.0, 0.0),
    ("gemini", "gemini-2.5-pro"): (1.25, 10.0),
    ("anthropic", "claude-haiku-4-5"): (1.0, 5.0),
    ("anthropic", "claude-sonnet-5"): (3.0, 15.0),
}


class AIRankingError(Exception):
    """Raised when the AI scorer can't produce a result for a job — callers
    should fall back to the heuristic scorer (ranker.score_job)."""


class _AIRankingSchema(BaseModel):
    is_job_posting: bool
    score: int
    skill_match: int
    contract_type_guess: Literal["b2b", "employment", "freelance", "unclear"]
    seniority_fit: Literal["under_qualified", "match", "over_qualified", "unclear"]
    reasons: list[str]
    red_flags: list[str]


def _estimate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float | None:
    pricing = _PRICING.get((provider, model))
    if pricing is None:
        return None
    input_price, output_price = pricing
    return (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price


class AIRanker:
    """One instance per pipeline run: holds the provider client, the profile
    (constant across every job), and accumulates run-level cost/usage stats."""

    def __init__(self, provider: LLMProvider, ai_config: AIConfig, profile: Profile) -> None:
        self._provider = provider
        self._ai_config = ai_config
        self._profile = profile
        self._disabled = False
        self._call_count = 0
        self._input_tokens = 0
        self._output_tokens = 0

    def score_job(self, job: Job) -> RankResult:
        """Raises AIRankingError if the AI scorer can't produce a result —
        callers should fall back to the heuristic scorer for this job."""
        if self._disabled:
            raise AIRankingError("AI ranker disabled for this run after a fatal provider error")

        system, user_message = self._build_prompt(job)

        result = None
        last_exc: Exception | None = None
        for attempt in range(self._ai_config.max_retries + 1):
            try:
                result = self._provider.complete_structured(
                    system=system,
                    user_message=user_message,
                    schema=_AIRankingSchema,
                    max_tokens=_MAX_OUTPUT_TOKENS,
                )
                break
            except FatalProviderError as exc:
                self._disabled = True
                logger.warning("AI provider disabled for the rest of this run: %s", exc)
                raise AIRankingError(str(exc)) from exc
            except TransientProviderError as exc:
                last_exc = exc
                if attempt < self._ai_config.max_retries:
                    time.sleep(min(2**attempt, 8))
        if result is None:
            raise AIRankingError(
                f"AI ranking failed after {self._ai_config.max_retries + 1} attempt(s): {last_exc}"
            ) from last_exc

        self._call_count += 1
        self._input_tokens += result.input_tokens
        self._output_tokens += result.output_tokens

        parsed: _AIRankingSchema = result.parsed
        score = max(0, min(100, parsed.score))
        reasons = list(parsed.reasons)
        red_flags = list(parsed.red_flags)
        if not parsed.is_job_posting:
            score = 0
            red_flags.append("AI flagged this listing as not a genuine job posting (possible spam)")

        return RankResult(
            score=score,
            skill_match=max(0, min(100, parsed.skill_match)),
            eligibility_confidence=eligibility_confidence_for(job.eligibility_bucket),
            # Same precedence as ranker.resolve_contract_type, with the AI's
            # reading of the prose standing in for the heuristic scan: a
            # source that states the engagement type as structured data
            # (Himalayas employmentType, Lever commitment, ...) is more
            # reliable than any inference from the description, so the
            # fetcher's value wins whenever it isn't UNCLEAR.
            contract_type_guess=(
                job.contract_type_guess
                if job.contract_type_guess != ContractTypeGuess.UNCLEAR
                else ContractTypeGuess(parsed.contract_type_guess)
            ),
            reasons=reasons,
            red_flags=red_flags,
            ranking_source=RankingSource.AI,
            seniority_fit=SeniorityFit(parsed.seniority_fit),
        )

    def _build_prompt(self, job: Job) -> tuple[str, str]:
        description = job.description[: self._ai_config.max_description_chars]
        system = (
            "You are an assistant scoring a remote job posting against a candidate's "
            "profile for a personal job-search tool called JobScout. Respond only "
            "through the structured schema provided — no other text."
        )
        user_message = json.dumps(
            {
                "job": {
                    "title": job.title,
                    "company": job.company,
                    "tags": job.tags,
                    "salary_text": job.salary_text,
                    "location_text": job.location_text,
                    "description": description,
                },
                "candidate_profile": {
                    "role_targets": self._profile.role_targets,
                    "core_skills": self._profile.core_skills,
                    "experience": self._profile.experience,
                    "languages": self._profile.languages,
                    "work_setup": self._profile.work_setup,
                    "location": self._profile.location,
                },
                "instructions": (
                    "First judge whether this is a genuine job posting, not spam, a "
                    "product announcement, or a scraped/broken page (is_job_posting). "
                    "Then score 0-100 how well this specific job matches the candidate "
                    "profile (score), what fraction of core_skills the posting evidences "
                    "(skill_match, 0-100), your best guess at the contracting structure "
                    "(contract_type_guess), and how the role's seniority compares to the "
                    "candidate's experience (seniority_fit). Give short reasons and any "
                    "red_flags worth a human's attention."
                ),
            }
        )
        return system, user_message

    def summary_line(self) -> str:
        if self._call_count == 0:
            return "AI ranker: no calls made this run"
        cost = _estimate_cost(self._ai_config.provider, self._ai_config.model, self._input_tokens, self._output_tokens)
        cost_str = f"${cost:.4f}" if cost is not None else "unknown (model not in local pricing table)"
        return (
            f"AI ranker: {self._call_count} call(s), {self._input_tokens} input / "
            f"{self._output_tokens} output tokens, estimated cost {cost_str}"
        )
