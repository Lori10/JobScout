"""Hacker News "Who is hiring?" fetcher — two-step via the Algolia HN Search API.

1. search_by_date for the latest "Ask HN: Who is hiring?" story.
2. fetch that story's item tree and treat only its TOP-LEVEL children as
   job posts (nested replies are discussion, not postings, and are ignored).

Comment bodies are unstructured freeform HTML with no enforced delimiter.
Parsing company/title is best-effort (many posters use "Company | Role |
Location" on the first line, but not all) — see README known limitations.
The full plain-text body is always kept in `description` regardless, so
nothing is lost even when structured parsing fails.
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
from collections.abc import Iterable

import requests

from jobscout.fetchers.ats_boards import detect_board, fetch_board_postings, posting_to_job, resolve_board
from jobscout.fetchers.common import extract_apply_url, extract_email, extract_href_urls, parse_iso_datetime
from jobscout.htmlutils import strip_html
from jobscout.models import ApplicationChannel, Job

logger = logging.getLogger(__name__)

SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
ITEM_URL_TEMPLATE = "https://hn.algolia.com/api/v1/items/{item_id}"
TIMEOUT = 20

# Some top-level comments in the hiring thread are job SEEKERS advertising
# themselves (the convention for that is a separate monthly "Who wants to
# be hired?" thread, but people cross-post). These use a distinctive
# "Field: value" self-profile format that real company postings don't —
# catching it here means we don't surface someone's résumé as a "job".
_CANDIDATE_PROFILE_RE = re.compile(r"willing to relocate\s*:|r[ée]sum[ée]\s*/?\s*cv\s*:", re.IGNORECASE)

# Heuristics for skipping non-role segments (salary, employment type,
# location/remote status, bare URLs) when picking which "|"-delimited
# segment after the company name is actually the role title — posters
# don't use a consistent field order, so the second segment is often NOT
# the role (e.g. "Company | 150-250k+ equity | Remote | Multiple roles").
_MONEY_RE = re.compile(r"\$\s?\d|\d[\d,]*\s?k\+?|equity|salary|/\s*year|/\s*hr\b|per\s+hour|per\s+year", re.IGNORECASE)
# fullmatch, not search: a segment like "Full-time - https://... GovStar
# builds ..." contains the word "Full-time" but is NOT purely an
# employment-type label, so it should fall through and be considered as a
# (messy but better-than-nothing) title candidate rather than get skipped.
_EMPLOYMENT_TYPE_ONLY_RE = re.compile(
    r"(full[\s-]?time|part[\s-]?time|contract(or)?s?|freelance|intern(ship)?)s?", re.IGNORECASE
)
_LOCATION_PREFIX_RE = re.compile(r"^(remote|onsite|on-site|hybrid)\b", re.IGNORECASE)
_URL_PREFIX_RE = re.compile(r"^https?://", re.IGNORECASE)

# resolve_board() fires for most of a thread's ~200+ comments (any link
# that isn't already a recognized board URL), each a separate HTTP request
# to a different company's site — sequential resolution measured ~100s on
# a real thread. Run them concurrently instead.
_RESOLVE_MAX_WORKERS = 15
_HN_PERMALINK_PREFIX = "https://news.ycombinator.com/item"


class HNWhoIsHiringFetcher:
    name = "hn_whoishiring"

    def fetch(self) -> list[Job]:
        story_id = self._find_latest_thread_id()
        if story_id is None:
            logger.warning("hn_whoishiring: could not find a current 'Who is hiring' thread")
            return []

        try:
            response = requests.get(ITEM_URL_TEMPLATE.format(item_id=story_id), timeout=TIMEOUT)
            response.raise_for_status()
            item = response.json()
        except Exception:
            logger.warning("hn_whoishiring: failed to fetch thread %s", story_id, exc_info=True)
            return []

        parsed: list[tuple[Job, dict]] = []
        for comment in item.get("children") or []:
            try:
                job = self._parse_comment(comment)
                if job is not None:
                    parsed.append((job, comment))
            except Exception:
                logger.warning("hn_whoishiring: skipping malformed comment", exc_info=True)

        resolved_cache = self._resolve_boards_concurrently(job for job, _ in parsed)

        jobs: list[Job] = []
        for job, comment in parsed:
            jobs.extend(self._expand_bundled_board(job, comment, resolved_cache))
        return jobs

    def _resolve_boards_concurrently(self, jobs: Iterable[Job]) -> dict[str, tuple[str, str] | None]:
        """Pre-resolves every job.url that isn't already a recognized board
        link (and isn't the HN-permalink fallback) in parallel, since
        resolve_board() makes one HTTP request per URL and a thread's worth
        of comments can have 100+ such URLs. A set naturally dedupes
        repeated URLs across comments too."""
        urls = {
            job.url
            for job in jobs
            if detect_board(job.url) is None and not job.url.startswith(_HN_PERMALINK_PREFIX)
        }
        if not urls:
            return {}
        cache: dict[str, tuple[str, str] | None] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=_RESOLVE_MAX_WORKERS) as pool:
            future_to_url = {pool.submit(resolve_board, url): url for url in urls}
            for future in concurrent.futures.as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    cache[url] = future.result()
                except Exception:
                    logger.debug("hn_whoishiring: resolve_board failed for %r", url, exc_info=True)
                    cache[url] = None
        return cache

    def _expand_bundled_board(
        self,
        job: Job,
        comment: dict,
        resolved_cache: dict[str, tuple[str, str] | None] | None = None,
    ) -> list[Job]:
        """If `job.url` is a bare Ashby/Greenhouse board-root link (bundles
        several distinct roles under one HN comment), replace it with one
        Job per role fetched from that ATS's public API. Falls back to the
        original single Job on any failure or empty result — this must
        never lose a posting outright."""
        board = detect_board(job.url)
        if board is None and not job.url.startswith(_HN_PERMALINK_PREFIX):
            # job.url is a real extracted link (not the HN-permalink
            # fallback used when no URL was found at all) that didn't
            # match a known board directly — worth checking whether it's a
            # vanity/redirect careers URL that 30x's to one (see
            # resolve_board's docstring). fetch() pre-resolves these
            # concurrently; a direct caller (e.g. tests) without a cache
            # falls back to resolving it here on the spot.
            if resolved_cache is not None:
                board = resolved_cache.get(job.url)
            else:
                board = resolve_board(job.url)
        if board is None:
            return [job]
        platform, slug = board
        postings = fetch_board_postings(platform, slug)
        if not postings:
            return [job]
        expanded = [
            posting_to_job(
                platform,
                raw,
                company=job.company,
                comment_id=comment.get("id"),
                fallback_posted_date=job.posted_date,
            )
            for raw in postings
        ]
        expanded = [j for j in expanded if j is not None]
        return expanded or [job]

    def _find_latest_thread_id(self) -> str | None:
        try:
            response = requests.get(
                SEARCH_URL,
                params={"query": '"Ask HN: Who is hiring"', "tags": "story"},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except Exception:
            logger.warning("hn_whoishiring: search request failed", exc_info=True)
            return None

        for hit in data.get("hits", []):
            title = (hit.get("title") or "").lower()
            if "who is hiring" in title and "wants to be hired" not in title and "freelancer" not in title:
                return hit.get("objectID")
        return None

    def _parse_comment(self, comment: dict) -> Job | None:
        if comment.get("deleted") or comment.get("dead") or not comment.get("text"):
            return None

        plain = strip_html(comment["text"])
        if not plain:
            return None
        if _CANDIDATE_PROFILE_RE.search(plain[:600]):
            return None

        first_line = plain.splitlines()[0]
        company, title = _parse_company_and_title(first_line)

        email = extract_email(plain)
        href_urls = extract_href_urls(comment["text"])
        apply_url = extract_apply_url(plain, href_urls)
        comment_id = comment.get("id")
        url = apply_url or f"https://news.ycombinator.com/item?id={comment_id}"

        if email:
            channel = ApplicationChannel.EMAIL
        elif apply_url:
            channel = ApplicationChannel.URL
        else:
            channel = ApplicationChannel.UNKNOWN

        posted_date = parse_iso_datetime(comment.get("created_at"), assume_utc=True)

        return Job(
            title=title,
            company=company,
            description=plain,
            url=url,
            source=self.name,
            posted_date=posted_date,
            tags=[],
            location_text=None,
            application_channel=channel,
            source_id=str(comment_id) if comment_id is not None else None,
        )


def _is_non_role_segment(segment: str) -> bool:
    """True if `segment` looks like salary, employment type, location/remote
    status, or a bare URL — i.e. NOT a role title, even though it commonly
    appears in the "role" position when posters don't lead with the role."""
    return bool(
        _MONEY_RE.search(segment)
        or _EMPLOYMENT_TYPE_ONLY_RE.fullmatch(segment)
        or _LOCATION_PREFIX_RE.match(segment)
        or _URL_PREFIX_RE.match(segment)
    )


def _parse_company_and_title(first_line: str) -> tuple[str, str]:
    parts = [p.strip() for p in first_line.split("|") if p.strip()]
    if len(parts) < 2:
        snippet = first_line.strip()[:80] or "HN Who's Hiring post"
        return snippet, snippet

    company = parts[0][:120]
    for segment in parts[1:]:
        if not _is_non_role_segment(segment):
            return company, segment[:120]
    # Nothing looked role-like (every field was salary/location/type/URL) —
    # this usually means the actual role(s) are listed further down in the
    # body (e.g. a bullet list of open positions), not on the first line.
    # Falling back to one of the rejected segments would silently
    # reintroduce exactly what was just filtered out (e.g. a location
    # string masquerading as a title), so use an honest placeholder
    # instead — the full text is preserved in description regardless.
    return company, "Role not stated in header — see description"
