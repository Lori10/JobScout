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

# Checked against a fixed window of words immediately preceding a phrase
# match (real case: "This role does NOT have an in-office requirement" —
# a plain substring match on "in-office" wrongly excluded a remote-friendly
# job). Apostrophes are stripped entirely by normalize_text (same rule as
# "U.S." -> "us"), so contracted forms appear here without one.
_NEGATION_WORDS = {
    "not",
    "no",
    "never",
    "without",
    "non",
    "dont",
    "doesnt",
    "didnt",
    "isnt",
    "arent",
    "wasnt",
    "werent",
    "wont",
    "cant",
    "cannot",
    "shouldnt",
    "neednt",
}
_NEGATION_WINDOW_WORDS = 5


def normalize_text(text: str) -> str:
    if not text:
        return ""
    lowered = text.lower()
    with_separators_as_space = _SEPARATOR_RE.sub(" ", lowered)
    no_punct = _PUNCT_RE.sub("", with_separators_as_space)
    return _WS_RE.sub(" ", no_punct).strip()


def _is_negated(text_norm: str, match_start: int) -> bool:
    preceding_words = text_norm[:match_start].split()
    window = preceding_words[-_NEGATION_WINDOW_WORDS:]
    return any(w in _NEGATION_WORDS for w in window)


def find_phrase_matches(text_norm: str, phrases: list[str]) -> list[str]:
    """Return the subset of `phrases` found as whole-word(s) matches in
    already-normalized `text_norm`. Matching is substring-anywhere but
    boundary-anchored, so short phrases like "ai" or "ml" don't fire inside
    unrelated words like "chain" or "html". A phrase counts only if at
    least one occurrence isn't immediately preceded by a negation word —
    a phrase appearing multiple times, negated in one place and not
    another, still counts."""
    matches = []
    for phrase in phrases:
        phrase_norm = normalize_text(phrase)
        if not phrase_norm:
            continue
        pattern = r"\b" + re.escape(phrase_norm) + r"\b"
        for m in re.finditer(pattern, text_norm):
            if not _is_negated(text_norm, m.start()):
                matches.append(phrase)
                break
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
    also be irrelevant, and vice versa.

    Title is checked first and, if it names a non-engineering function
    (sales, marketing, design, support, ...), wins outright: company or
    product boilerplate frequently mentions "AI" even when hiring for an
    unrelated role (e.g. "Lite Agent is a privacy-first AI browser
    copilot" hiring a commission-based sales affiliate), so a bare
    keyword hit elsewhere in the text is a much weaker signal than what
    the title itself says the role is.
    """
    title_norm = normalize_text(job.title)
    if find_phrase_matches(title_norm, config.non_role_title_phrases):
        return replace(job, is_relevant=False)

    text_norm = job_relevance_text(job)
    if find_phrase_matches(text_norm, config.relevance_keywords):
        return replace(job, is_relevant=True)

    # Bare "ai"/"ml" hits are too generic to trust anywhere in the body
    # (real case: "please don't send me 5 paragraphs of AI text" made an
    # unrelated Account Executive/PM posting look relevant) - only count
    # them when they're in the TITLE, a deliberate role label rather than
    # incidental text.
    weak_title_hits = find_phrase_matches(title_norm, config.relevance_keywords_weak)
    return replace(job, is_relevant=bool(weak_title_hits))
