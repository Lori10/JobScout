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

import logging
import re

import requests

from jobscout.fetchers.common import extract_email, extract_url, parse_iso_datetime
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

        jobs: list[Job] = []
        for comment in item.get("children") or []:
            try:
                job = self._parse_comment(comment)
                if job is not None:
                    jobs.append(job)
            except Exception:
                logger.warning("hn_whoishiring: skipping malformed comment", exc_info=True)
        return jobs

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
        apply_url = extract_url(plain)
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
