"""Cross-source deduplication: exact normalized-URL match, plus fuzzy
(company + title) matching for the same posting appearing on multiple
boards under different URLs (e.g. a company's own careers page + an
aggregator mirror)."""

from __future__ import annotations

from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit

from jobscout.filters import normalize_text
from jobscout.models import Job

FUZZY_THRESHOLD = 0.88

# Company legal-entity suffixes and common title abbreviations that would
# otherwise make an obvious duplicate ("Acme Inc" / "Sr. LLM Engineer" vs
# "Acme" / "Senior LLM Engineer") score as dissimilar under raw string
# comparison.
_COMPANY_SUFFIX_TOKENS = {"inc", "llc", "ltd", "gmbh", "corp", "corporation", "co"}
_TITLE_ABBREVIATIONS = {"sr": "senior", "jr": "junior"}


def normalize_url(url: str | None) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path.rstrip("/")
    return urlunsplit(("https", netloc, path, "", ""))


_MIN_COMPANY_SIMILARITY = 0.5


def _company_key(job: Job) -> str:
    tokens = [t for t in normalize_text(job.company).split() if t not in _COMPANY_SUFFIX_TOKENS]
    return " ".join(tokens)


def _fuzzy_key(job: Job) -> str:
    title_tokens = [_TITLE_ABBREVIATIONS.get(t, t) for t in normalize_text(job.title).split()]
    return " ".join([_company_key(job)] + title_tokens)


def _fuzzy_similarity(a: Job, b: Job) -> float:
    key_a, key_b = _fuzzy_key(a), _fuzzy_key(b)
    if not key_a or not key_b:
        return 0.0

    # A long SHARED title (e.g. two unrelated postings that both fell back
    # to the same generic "role not stated" placeholder) can dominate the
    # combined-string ratio even when the companies are completely
    # different. Require the company names to be reasonably similar on
    # their own too, so shared boilerplate/title text can never by itself
    # cause two different companies' postings to be merged.
    company_a, company_b = _company_key(a), _company_key(b)
    if not company_a or not company_b:
        return 0.0
    company_similarity = SequenceMatcher(None, company_a, company_b).ratio()
    if company_similarity < _MIN_COMPANY_SIMILARITY:
        return 0.0

    return SequenceMatcher(None, key_a, key_b).ratio()


def _prefer_richer(a: Job, b: Job) -> Job:
    """When two records collide, keep whichever has the more complete
    description, merging tags from both so nothing is lost."""
    richer, other = (a, b) if len(a.description or "") >= len(b.description or "") else (b, a)
    richer.tags = sorted(set(richer.tags) | set(other.tags))
    return richer


def dedupe(jobs: list[Job]) -> list[Job]:
    kept: list[Job] = []
    seen_urls: dict[str, int] = {}

    for job in jobs:
        norm_url = normalize_url(job.url)
        job.dedup_key = norm_url or f"{job.source}:{job.source_id or job.title}"

        match_index: int | None = None
        if norm_url and norm_url in seen_urls:
            match_index = seen_urls[norm_url]
        else:
            for i, existing in enumerate(kept):
                if _fuzzy_similarity(job, existing) >= FUZZY_THRESHOLD:
                    match_index = i
                    break

        if match_index is None:
            kept.append(job)
            if norm_url:
                seen_urls[norm_url] = len(kept) - 1
        else:
            kept[match_index] = _prefer_richer(kept[match_index], job)

    return kept
