"""Stage 1 (hard eligibility) and Stage 2 (keyword relevance) filtering.

Every function here is pure: no filesystem/network access, no logging side
effects. Config (phrase lists) and profile data are passed in by the caller
(pipeline.py loads config.yaml/profile.yaml once and threads it through).
This is what makes the tricky eligibility cases exhaustively unit-testable
with plain string fixtures.
"""

from __future__ import annotations

import re
from dataclasses import replace

from jobscout.config import FilterConfig
from jobscout.models import EligibilityBucket, Job

# Hyphens/underscores/slashes are treated as word separators (so "US-based"
# and "US based" normalize identically); all other punctuation is dropped
# entirely (so "U.S." normalizes to "us", not "u s") — this ordering is what
# lets phrase lists in config.yaml be written in natural prose without
# needing every punctuation variant spelled out.
_SEPARATOR_RE = re.compile(r"[-_/]")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    if not text:
        return ""
    lowered = text.lower()
    with_separators_as_space = _SEPARATOR_RE.sub(" ", lowered)
    no_punct = _PUNCT_RE.sub("", with_separators_as_space)
    return _WS_RE.sub(" ", no_punct).strip()


def find_phrase_matches(text_norm: str, phrases: list[str]) -> list[str]:
    """Return the subset of `phrases` found as whole-word(s) matches in
    already-normalized `text_norm`. Matching is substring-anywhere but
    boundary-anchored, so short phrases like "ai" or "ml" don't fire inside
    unrelated words like "chain" or "html"."""
    matches = []
    for phrase in phrases:
        phrase_norm = normalize_text(phrase)
        if not phrase_norm:
            continue
        pattern = r"\b" + re.escape(phrase_norm) + r"\b"
        if re.search(pattern, text_norm):
            matches.append(phrase)
    return matches


def job_eligibility_text(job: Job) -> str:
    return normalize_text(" ".join([job.title, job.description, job.location_text or ""]))


def job_relevance_text(job: Job) -> str:
    return normalize_text(" ".join([job.title, job.description]))


def apply_eligibility_filter(job: Job, config: FilterConfig) -> Job:
    """Classify into eligible / excluded / needs_review, storing the exact
    matched phrase(s) as the audit trail. Hard excludes are checked before
    needs_review, so a post matching both wins as excluded."""
    text_norm = job_eligibility_text(job)

    exclude_hits = find_phrase_matches(text_norm, config.exclude_phrases)
    if exclude_hits:
        return replace(
            job,
            eligibility_bucket=EligibilityBucket.EXCLUDED,
            eligibility_reason=f"excluded: matched {exclude_hits}",
        )

    hybrid_hits = find_phrase_matches(text_norm, config.hybrid_onsite_phrases)
    if hybrid_hits:
        remote_hits = find_phrase_matches(text_norm, config.remote_indicator_phrases)
        if not remote_hits:
            return replace(
                job,
                eligibility_bucket=EligibilityBucket.EXCLUDED,
                eligibility_reason=f"excluded: matched {hybrid_hits} with no remote indicator present",
            )

    review_hits = find_phrase_matches(text_norm, config.needs_review_phrases)
    if review_hits:
        return replace(
            job,
            eligibility_bucket=EligibilityBucket.NEEDS_REVIEW,
            eligibility_reason=f"needs_review: matched {review_hits}",
        )

    return replace(job, eligibility_bucket=EligibilityBucket.ELIGIBLE, eligibility_reason=None)


def apply_keyword_relevance_filter(job: Job, config: FilterConfig) -> Job:
    """Stage 2: independent of eligibility_bucket — an excluded job can
    also be irrelevant, and vice versa."""
    text_norm = job_relevance_text(job)
    hits = find_phrase_matches(text_norm, config.relevance_keywords)
    return replace(job, is_relevant=bool(hits))
