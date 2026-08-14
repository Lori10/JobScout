"""Small helpers shared across fetchers: email/URL extraction for
application_channel inference, date parsing (ISO8601, RFC-822 for RSS, and
unix epoch), and mapping a source's own structured employment-type field to
ContractTypeGuess."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from jobscout.filters import normalize_text
from jobscout.models import ApplicationChannel, ContractTypeGuess

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
URL_RE = re.compile(r"https?://[^\s)>\]]+")
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)

# Known ATS/job-board domains: when one of these appears anywhere in a
# posting, it's almost always the actual apply link, not incidental.
_ATS_DOMAINS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "workable.com",
    "bamboohr.com",
    "breezy.hr",
    "smartrecruiters.com",
    "myworkdayjobs.com",
    "recruitee.com",
    "personio.com",
    "jobvite.com",
    "icims.com",
    "taleo.net",
)
# A URL whose own path names it as the careers/apply page.
_APPLY_PATH_RE = re.compile(r"/(careers?|jobs?|apply|positions?|join(-us)?)(/|$|\?)", re.IGNORECASE)
# Cue phrases that, immediately before a URL, mark it as the apply link
# rather than an incidental blog/handbook/about-page link mentioned earlier
# in the same freeform text.
_APPLY_CONTEXT_RE = re.compile(r"apply|careers?|open\s+roles?|open\s+positions?|job\s+board|hiring\s+page", re.IGNORECASE)
_APPLY_CONTEXT_WINDOW_CHARS = 60


def extract_email(text: str | None) -> str | None:
    match = EMAIL_RE.search(text or "")
    return match.group(0) if match else None


def extract_url(text: str | None) -> str | None:
    match = URL_RE.search(text or "")
    return match.group(0).rstrip(".,;:") if match else None


def extract_href_urls(raw_html: str | None) -> list[str]:
    """All <a href="..."> URLs from raw (unstripped) HTML, HTML-unescaped
    and trailing-punctuation-stripped like extract_url(). Some platforms
    (HN included) visually truncate a long URL's *displayed* text with an
    ellipsis while keeping the real, complete URL in href — since
    htmlutils.strip_html() discards tag attributes and keeps only the
    (possibly truncated) visible text, plain-text URL extraction alone can
    end up with the truncated display version. extract_apply_url() uses
    this to recover the real URL when that happens."""
    if not raw_html:
        return []
    return [html.unescape(m.group(1)).rstrip(".,;:") for m in _HREF_RE.finditer(raw_html)]


def extract_apply_url(text: str | None, href_urls: list[str] | None = None) -> str | None:
    """Like extract_url, but prefers a URL that actually looks like an
    apply/careers link over whichever URL happens to appear first in
    freeform text. Real posts often link to an unrelated blog/about/
    handbook page before the actual apply link (found via live audit: an
    HN "who is hiring" post led with "Why we joined ClickHouse: <blog
    link>" and only mentioned the real .../careers link — in an explicit
    "apply to the one you think fits" sentence — three sentences later;
    the naive first-URL-in-text approach picked the blog post instead).
    Falls back to the first URL in the text when nothing scores higher,
    so behavior for posts with only one URL (the common case) is
    unchanged.

    `href_urls` (see extract_href_urls) lets a caller supply the real,
    untruncated URLs from the original HTML — if a candidate found in
    `text` is a truncated prefix of one of these (real case: HN displayed
    "...b62..." for a URL whose href was the complete
    "...b62365ff5fc2", and the truncated version 404'd), the full href is
    used instead of the truncated text match."""
    matches = list(URL_RE.finditer(text or ""))
    if not matches:
        return None

    def resolve(match: re.Match) -> str:
        candidate = match.group(0).rstrip(".,;:")
        if href_urls:
            for href in href_urls:
                if href.startswith(candidate) and len(href) > len(candidate):
                    return href
        return candidate

    def score(match: re.Match) -> int:
        url = resolve(match)
        if any(domain in url.lower() for domain in _ATS_DOMAINS):
            return 3
        if _APPLY_PATH_RE.search(url):
            return 2
        preceding = (text or "")[max(0, match.start() - _APPLY_CONTEXT_WINDOW_CHARS) : match.start()]
        if _APPLY_CONTEXT_RE.search(preceding):
            return 1
        return 0

    best = max(matches, key=score)
    return resolve(best)


def infer_application_channel(description: str, has_url: bool) -> ApplicationChannel:
    if has_url:
        return ApplicationChannel.URL
    if extract_email(description):
        return ApplicationChannel.EMAIL
    return ApplicationChannel.UNKNOWN


def parse_iso_datetime(value: str | None, assume_utc: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None and assume_utc:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_rfc822_datetime(value: str | None, assume_utc: bool = True) -> datetime | None:
    """RSS <pubDate> format ("Wed, 22 Jul 2026 07:02:04 +0000"), which
    datetime.fromisoformat cannot parse. Stdlib only — the project has no
    RSS/XML dependency and doesn't need one."""
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None and assume_utc:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_unix_timestamp(value, *, milliseconds: bool = False) -> datetime | None:
    """Epoch timestamps, always returned as tz-aware UTC. Several sources
    use seconds (Himalayas pubDate, Arbeitnow created_at) while Lever's
    createdAt is milliseconds — passing the wrong unit silently yields a
    date in 1970 or the year 58000 rather than an error, so the unit is an
    explicit keyword rather than something guessed from magnitude."""
    if value is None or isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if milliseconds:
        seconds /= 1000.0
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


# Structured employment-type labels, normalized via filters.normalize_text
# (lowercased, "-"/"_"/"/" -> space, other punctuation dropped) before
# matching. Real values are inconsistent even within a single source —
# Arbeitnow alone emits "Full-Time", "Full time", "parttime fixed term",
# "Fixed term" and "" — so these are substring checks against the
# normalized label, not exact matches.
#
# FREELANCE is checked before EMPLOYMENT because a label like
# "Contract, full-time hours" is a contract engagement first; the same
# priority ranker.guess_contract_type uses for prose.
_FREELANCE_LABEL_TOKENS = (
    "contractor",
    "contract",
    "freelance",
    "freelancer",
    "freiberuflich",
    "freiberufler",
    "temporary",
    "fixed term",
    "interim",
    "consultant",
    "1099",
)
_EMPLOYMENT_LABEL_TOKENS = (
    "full time",
    "fulltime",
    "part time",
    "parttime",
    "permanent",
    "festanstellung",
    "unbefristet",
    "employee",
    "internship",
    "intern",
    "werkstudent",
)


# Values that already mean "no restriction" — appending "only" to these
# would invert their meaning ("Anywhere only").
_UNRESTRICTED_LOCATION_TOKENS = ("anywhere", "worldwide", "global", "remote", "any location")


def format_location_restrictions(values) -> str | None:
    """Renders a source's structured hiring-location ALLOW-LIST into
    location_text, preserving the fact that it is a restriction.

    Himalayas (`locationRestrictions`) and Jobicy (`jobGeo`) both state
    where a company will hire as structured data, but as bare country names
    ("United States", "USA", "Europe"). Stage 1 reads text, so a bare name
    is indistinguishable from a passing mention and the posting sails
    through as eligible — measured live, that left 79 of 227 Himalayas
    postings and 35 of 100 Jobicy postings marked eligible while the source
    itself said US-only.

    Appending "only" restores what the field actually asserts, and lands
    the result in vocabulary config.yaml already has: "USA" -> "USA only"
    matches exclude_phrases, "Europe" -> "Europe only" matches
    needs_review_phrases. Values that already mean unrestricted are passed
    through untouched.

    This does NOT fully solve the problem, and is not meant to: an
    allow-list naming any of ~40 other countries still needs a matching
    phrase in config.yaml to be caught. The general rule — "the source
    named an explicit country list and Albania isn't in it" — is structural
    and can't be expressed as a phrase; see README known limitations.
    """
    if isinstance(values, str):
        values = [values]
    parts = [str(v).strip() for v in (values or []) if v and str(v).strip()]
    if not parts:
        return None

    rendered = ", ".join(parts)
    lowered = rendered.lower()
    if "only" in lowered or any(token in lowered for token in _UNRESTRICTED_LOCATION_TOKENS):
        return rendered
    return f"{rendered} only"


def format_salary_range(minimum, maximum, currency: str | None = None, period: str | None = None) -> str | None:
    """Renders the min/max/currency/period salary quartet several Phase 4
    sources expose under different key names (Himalayas
    minSalary/maxSalary/currency/salaryPeriod, Jobicy
    salaryMin/salaryMax/salaryCurrency/salaryPeriod).

    `period` is carried into the output rather than dropped because these
    sources quote hourly rates as often as annual ones — "85" means very
    different things as a yearly salary and an hourly rate, and hourly is
    what the contract/freelance postings tend to use.
    """
    try:
        low = int(minimum or 0)
        high = int(maximum or 0)
    except (TypeError, ValueError):
        return None
    if not low and not high:
        return None

    currency = (currency or "").strip()
    prefix = "$" if currency == "USD" else ""
    suffix = f" {currency}" if currency and not prefix else ""

    if low and high and low != high:
        amount = f"{prefix}{low:,} - {prefix}{high:,}"
    else:
        amount = f"{prefix}{(low or high):,}"

    period = (period or "").strip()
    return f"{amount}{suffix}{f' ({period})' if period else ''}"


def contract_type_from_label(label) -> ContractTypeGuess:
    """Maps a source's own employment-type field to ContractTypeGuess.

    A source that states the engagement type as structured data is far more
    trustworthy than ranker.guess_contract_type's scan of the description
    prose, which has to infer it from phrases that may belong to boilerplate
    (see ranker.resolve_contract_type for how the two are reconciled).

    Accepts a string or a list of strings — several sources give an array
    (Arbeitnow `job_types`, Jobicy `jobType`) — in which case the first
    non-UNCLEAR match wins.
    """
    if label is None:
        return ContractTypeGuess.UNCLEAR
    if isinstance(label, (list, tuple, set)):
        for item in label:
            guess = contract_type_from_label(item)
            if guess != ContractTypeGuess.UNCLEAR:
                return guess
        return ContractTypeGuess.UNCLEAR
    if not isinstance(label, str):
        return ContractTypeGuess.UNCLEAR

    normalized = normalize_text(label)
    if not normalized:
        return ContractTypeGuess.UNCLEAR
    if any(token in normalized for token in _FREELANCE_LABEL_TOKENS):
        return ContractTypeGuess.FREELANCE
    if any(token in normalized for token in _EMPLOYMENT_LABEL_TOKENS):
        return ContractTypeGuess.EMPLOYMENT
    return ContractTypeGuess.UNCLEAR
