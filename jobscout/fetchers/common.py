"""Small helpers shared across fetchers: email/URL extraction for
application_channel inference, and ISO8601 date parsing."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from jobscout.models import ApplicationChannel

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
