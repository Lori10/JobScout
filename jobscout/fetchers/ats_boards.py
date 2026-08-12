"""Ashby/Greenhouse public job-board APIs (Phase 4: PLAN.md's "expanding
bundled multi-role HN comments"). A single HN "who is hiring" comment often
links to a company's whole careers board rather than one specific role
(e.g. https://jobs.ashbyhq.com/starbridge lists 10 open roles) — hn_
whoishiring.py uses this module to expand that one comment into one Job
per listed role via the ATS's own public, unauthenticated JSON API (not
scraping), instead of judging relevance on the comment's aggregate text.

A URL with an extra path segment (e.g. .../starbridge/117e56db-...) already
names one specific role and is intentionally left untouched by detect_board
— only bare board-root links are bundles.
"""

from __future__ import annotations

import logging
import re

import requests

from jobscout.fetchers.common import parse_iso_datetime
from jobscout.htmlutils import strip_html
from jobscout.models import ApplicationChannel, Job

logger = logging.getLogger(__name__)

TIMEOUT = 20

# Short timeout for the speculative redirect-resolution request in
# resolve_board() — this fires for every HN posting whose link isn't
# already a recognized board URL, so one slow/unresponsive company site
# must not meaningfully delay the whole fetch.
_RESOLVE_TIMEOUT = 6

# Large companies can list hundreds of roles (real example: a well-known
# fintech's Greenhouse board had 565 open jobs) — capping keeps one bundled
# HN comment from flooding the pipeline. Normal eligibility/relevance
# filtering still applies to whichever roles are kept.
_MAX_ROLES_PER_BOARD = 40

_ASHBY_BOARD_ROOT_RE = re.compile(r"^https?://jobs\.ashbyhq\.com/([^/?#]+)/?$", re.IGNORECASE)
_GREENHOUSE_BOARD_ROOT_RE = re.compile(r"^https?://(?:boards|job-boards)\.greenhouse\.io/([^/?#]+)/?$", re.IGNORECASE)
# Broader than the two above (no anchoring to "nothing after the board
# slug") — matches ANY URL already hosted on a known ATS, including an
# already-specific job link. Used only to skip resolve_board's redirect
# request when it plainly can't help (a URL already on one of these hosts
# that isn't a board root is already specific, not a redirect-hiding bundle).
_KNOWN_ATS_HOST_RE = re.compile(r"^https?://(?:jobs\.ashbyhq\.com|(?:boards|job-boards)\.greenhouse\.io)/", re.IGNORECASE)


def detect_board(url: str | None) -> tuple[str, str] | None:
    """Returns (platform, board_slug) if `url` is a bare Ashby/Greenhouse
    board-root link, else None."""
    if not url:
        return None
    match = _ASHBY_BOARD_ROOT_RE.match(url)
    if match:
        return "ashby", match.group(1)
    match = _GREENHOUSE_BOARD_ROOT_RE.match(url)
    if match:
        return "greenhouse", match.group(1)
    return None


def resolve_board(url: str | None) -> tuple[str, str] | None:
    """Like detect_board, but for a URL that isn't already a recognized
    board link directly — follows redirects (a company's own vanity/
    redirect careers URL, e.g. https://starbridge.ai/careers, that 30x's
    to https://jobs.ashbyhq.com/starbridge) and re-checks the resolved
    URL. Never raises; returns None on any failure or when the resolved
    URL still isn't a recognized board. Callers should only invoke this
    when detect_board(url) already returned None, since it costs a real
    HTTP request. Skips the request entirely (returns None immediately)
    for a URL already hosted on a known ATS — if detect_board() didn't
    match it, it's already a specific job link, not a redirect-hiding
    bundle, and following it would just waste a request."""
    if not url or _KNOWN_ATS_HOST_RE.match(url):
        return None
    try:
        response = requests.get(url, allow_redirects=True, timeout=_RESOLVE_TIMEOUT, stream=True)
        response.close()
    except Exception:
        logger.debug("ats_boards: could not resolve redirects for %r", url, exc_info=True)
        return None
    return detect_board(response.url)


def fetch_board_postings(platform: str, board_slug: str) -> list[dict]:
    """Never raises — an ATS API hiccup falls back to the original single
    comment-as-Job behavior (see hn_whoishiring._expand_bundled_board)."""
    if platform == "ashby":
        return _fetch_ashby(board_slug)
    if platform == "greenhouse":
        return _fetch_greenhouse(board_slug)
    return []


def _fetch_ashby(board_slug: str) -> list[dict]:
    try:
        response = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{board_slug}", timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except Exception:
        logger.warning("ats_boards: failed to fetch ashby board %r", board_slug, exc_info=True)
        return []
    postings = [j for j in (data.get("jobs") or []) if j.get("isListed", True)]
    return postings[:_MAX_ROLES_PER_BOARD]


def _fetch_greenhouse(board_slug: str) -> list[dict]:
    try:
        response = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{board_slug}/jobs",
            params={"content": "true"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        logger.warning("ats_boards: failed to fetch greenhouse board %r", board_slug, exc_info=True)
        return []
    return (data.get("jobs") or [])[:_MAX_ROLES_PER_BOARD]


def posting_to_job(platform: str, raw: dict, *, company: str, comment_id, fallback_posted_date) -> Job | None:
    """Returns None (never raises) for a malformed posting missing a
    url/title — the caller drops it rather than storing a useless Job."""
    if platform == "ashby":
        return _ashby_posting_to_job(raw, company, comment_id, fallback_posted_date)
    if platform == "greenhouse":
        return _greenhouse_posting_to_job(raw, company, comment_id, fallback_posted_date)
    return None


def _ashby_posting_to_job(raw: dict, company: str, comment_id, fallback_posted_date) -> Job | None:
    url = raw.get("jobUrl") or raw.get("applyUrl")
    title = raw.get("title")
    if not url or not title:
        return None
    description = raw.get("descriptionPlain") or strip_html(raw.get("descriptionHtml") or "")
    posted_date = parse_iso_datetime(raw.get("publishedAt"), assume_utc=True) or fallback_posted_date
    tags = [t for t in (raw.get("department"), raw.get("team")) if t]
    return Job(
        title=title.strip(),
        company=company,
        description=description,
        url=url,
        source="hn_whoishiring",
        posted_date=posted_date,
        tags=tags,
        location_text=raw.get("location"),
        application_channel=ApplicationChannel.URL,
        source_id=f"{comment_id}:ashby:{raw.get('id')}",
    )


def _greenhouse_posting_to_job(raw: dict, company: str, comment_id, fallback_posted_date) -> Job | None:
    url = raw.get("absolute_url")
    title = raw.get("title")
    if not url or not title:
        return None
    description = strip_html(raw.get("content") or "")
    posted_date = (
        parse_iso_datetime(raw.get("first_published"), assume_utc=True)
        or parse_iso_datetime(raw.get("updated_at"), assume_utc=True)
        or fallback_posted_date
    )
    tags = [d.get("name") for d in (raw.get("departments") or []) if d.get("name")]
    location = (raw.get("location") or {}).get("name")
    return Job(
        title=title.strip(),
        company=company,
        description=description,
        url=url,
        source="hn_whoishiring",
        posted_date=posted_date,
        tags=tags,
        location_text=location,
        application_channel=ApplicationChannel.URL,
        source_id=f"{comment_id}:greenhouse:{raw.get('id')}",
    )
