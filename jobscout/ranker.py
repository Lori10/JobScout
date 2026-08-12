"""Heuristic scorer (Phase 1). Produces the same output shape Phase 3's AI
scorer will later produce (score/skill_match/eligibility_confidence/
contract_type_guess/reasons/red_flags/ranking_source), so pipeline.py and
report.py never need to know which scorer produced a result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from jobscout.config import FilterConfig, Profile, RankerConfig
from jobscout.filters import find_phrase_matches, job_relevance_text, normalize_text
from jobscout.models import ContractTypeGuess, EligibilityBucket, Job, RankingSource, SeniorityFit

# Rule-based, fixed-priority keyword scan for contract_type_guess. B2B is
# checked first so "B2B contract" resolves to B2B, not FREELANCE; FREELANCE
# before EMPLOYMENT so "contractor, full-time hours" resolves to FREELANCE
# rather than the much weaker generic "full-time" employment signal.
#
# "contractor"/"full time" alone (not just the longer compound phrases) are
# included deliberately: real postings almost never write the redundant
# "full-time employment" or "contract position" — they just say "Full-time"
# or "Contractor" — and requiring the longer phrase meant this guess was
# "unclear" for the vast majority of real postings (found via live audit:
# 25/26 ranked jobs were "unclear", several with "contractor"/"full-time"
# right there in the text that the old phrase list never matched).
_B2B_PHRASES = ["b2b", "invoice", "registered company", "own company", "corp to corp", "c2c"]
_FREELANCE_PHRASES = [
    "freelance",
    "freelancer",
    "1099",
    "contractor",
    "contract role",
    "contract position",
    "freiberufler",
]
_EMPLOYMENT_PHRASES = ["full-time employment", "permanent position", "full time employee", "permanent role", "full time"]

# Public: reused by ai_ranker.py so eligibility_confidence stays a single
# deterministic mapping owned here, rather than something an LLM guesses.
ELIGIBILITY_CONFIDENCE_BY_BUCKET = {
    EligibilityBucket.ELIGIBLE: 100,
    EligibilityBucket.NEEDS_REVIEW: 60,
    EligibilityBucket.EXCLUDED: 0,
}


def eligibility_confidence_for(bucket: EligibilityBucket) -> int:
    return ELIGIBILITY_CONFIDENCE_BY_BUCKET.get(bucket, 50)


@dataclass
class RankResult:
    score: int
    skill_match: int
    eligibility_confidence: int
    contract_type_guess: ContractTypeGuess
    reasons: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    ranking_source: RankingSource = RankingSource.HEURISTIC
    seniority_fit: SeniorityFit | None = None  # AI-only; heuristic scorer leaves this None


def guess_contract_type(text_norm: str) -> ContractTypeGuess:
    if find_phrase_matches(text_norm, _B2B_PHRASES):
        return ContractTypeGuess.B2B
    if find_phrase_matches(text_norm, _FREELANCE_PHRASES):
        return ContractTypeGuess.FREELANCE
    if find_phrase_matches(text_norm, _EMPLOYMENT_PHRASES):
        return ContractTypeGuess.EMPLOYMENT
    return ContractTypeGuess.UNCLEAR


def _keyword_group_score(text_norm: str, ranker_config: RankerConfig) -> tuple[float, list[str]]:
    """Each keyword group contributes its full weight if any of its phrases
    match (not per-phrase), normalized to a 0-40 point contribution so
    LLM/RAG-heavy groups clearly outscore generic-Python-only groups."""
    if not ranker_config.keyword_groups:
        return 0.0, []
    total_weight = sum(g.weight for g in ranker_config.keyword_groups.values()) or 1.0
    matched_weight = 0.0
    reasons = []
    for name, group in ranker_config.keyword_groups.items():
        hits = find_phrase_matches(text_norm, group.phrases)
        if hits:
            matched_weight += group.weight
            reasons.append(f"keyword group '{name}' matched (weight {group.weight}): {hits}")
    return (matched_weight / total_weight) * 40.0, reasons


def _recency_bonus(posted_date: datetime | None, recency_bonus_max: int) -> tuple[float, str | None]:
    if posted_date is None:
        return 0.0, None
    now = datetime.now(timezone.utc)
    reference = posted_date if posted_date.tzinfo else posted_date.replace(tzinfo=timezone.utc)
    age_days = (now - reference).total_seconds() / 86400
    if age_days < 0:
        age_days = 0
    if age_days <= 3:
        return recency_bonus_max * 1.0, f"posted {age_days:.1f} days ago (recent)"
    if age_days <= 7:
        return recency_bonus_max * 0.5, f"posted {age_days:.1f} days ago"
    if age_days <= 14:
        return recency_bonus_max * 0.2, f"posted {age_days:.1f} days ago"
    return 0.0, None


def score_job(job: Job, ranker_config: RankerConfig, filter_config: FilterConfig, profile: Profile) -> RankResult:
    """Only meaningful for jobs that are relevant and not hard-excluded —
    callers should use trivial_rank_result() for excluded/irrelevant jobs."""
    text_norm = job_relevance_text(job)
    title_norm = normalize_text(job.title)

    reasons: list[str] = []
    red_flags: list[str] = []

    keyword_score, keyword_reasons = _keyword_group_score(text_norm, ranker_config)
    reasons.extend(keyword_reasons)

    title_hits = find_phrase_matches(title_norm, profile.role_targets)
    title_bonus = float(ranker_config.title_match_bonus) if title_hits else 0.0
    if title_hits:
        reasons.append(f"title matches role target(s): {title_hits}")

    positive_hits = find_phrase_matches(text_norm, filter_config.positive_phrases)
    positive_bonus = min(float(ranker_config.positive_signal_bonus_cap), 2.0 * len(positive_hits))
    if positive_hits:
        reasons.append(f"positive signals: {positive_hits}")

    recency_bonus, recency_reason = _recency_bonus(job.posted_date, ranker_config.recency_bonus_max)
    if recency_reason:
        reasons.append(recency_reason)

    penalty = 0.0
    if job.eligibility_bucket == EligibilityBucket.NEEDS_REVIEW:
        penalty = float(ranker_config.near_miss_penalty)
        red_flags.append("needs_review bucket: eligibility ambiguous, score penalized")

    max_possible = 40.0 + ranker_config.title_match_bonus + ranker_config.positive_signal_bonus_cap + ranker_config.recency_bonus_max
    raw = keyword_score + title_bonus + positive_bonus + recency_bonus - penalty
    score = round((raw / max_possible) * 100) if max_possible else 0
    score = max(0, min(100, score))
    if job.eligibility_bucket == EligibilityBucket.NEEDS_REVIEW:
        # A relevant needs_review job must never look identical to an
        # excluded/irrelevant one (trivial_rank_result always gives those
        # score=0) - otherwise the one bucket that's supposed to stay
        # visible for manual review sinks to the bottom indistinguishably
        # from jobs that were never ranked at all (found via live audit:
        # a real PostHog/EMEA posting scored exactly 0 after the near-miss
        # penalty pushed its already-weak raw score negative).
        score = max(score, 1)

    skill_hits = find_phrase_matches(text_norm, profile.core_skills)
    skill_match = round(100 * len(skill_hits) / len(profile.core_skills)) if profile.core_skills else 0
    skill_match = max(0, min(100, skill_match))
    if skill_hits:
        reasons.append(f"skill match: {skill_hits}")
    else:
        red_flags.append("no core skills from profile found in description")

    eligibility_confidence = eligibility_confidence_for(job.eligibility_bucket)

    contract_type_guess = guess_contract_type(text_norm)

    return RankResult(
        score=score,
        skill_match=skill_match,
        eligibility_confidence=eligibility_confidence,
        contract_type_guess=contract_type_guess,
        reasons=reasons,
        red_flags=red_flags,
        ranking_source=RankingSource.HEURISTIC,
    )


def trivial_rank_result(job: Job) -> RankResult:
    """Used for excluded/irrelevant jobs: stored, but sinks to the bottom
    of any score-sorted view rather than being ranked meaningfully."""
    if job.eligibility_bucket == EligibilityBucket.EXCLUDED:
        reason = f"not ranked: excluded ({job.eligibility_reason})"
    elif not job.is_relevant:
        reason = "not ranked: irrelevant to profile"
    else:
        reason = "not ranked"
    return RankResult(
        score=0,
        skill_match=0,
        eligibility_confidence=eligibility_confidence_for(job.eligibility_bucket),
        contract_type_guess=ContractTypeGuess.UNCLEAR,
        reasons=[reason],
        red_flags=[],
        ranking_source=RankingSource.HEURISTIC,
    )
