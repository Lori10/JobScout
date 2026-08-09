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

import requests

from jobscout.fetchers.common import extract_email, extract_url, parse_iso_datetime
from jobscout.htmlutils import strip_html
from jobscout.models import ApplicationChannel, Job

logger = logging.getLogger(__name__)

SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
ITEM_URL_TEMPLATE = "https://hn.algolia.com/api/v1/items/{item_id}"
TIMEOUT = 20


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


def _parse_company_and_title(first_line: str) -> tuple[str, str]:
    parts = [p.strip() for p in first_line.split("|") if p.strip()]
    if len(parts) >= 2:
        return parts[0][:120], parts[1][:120]
    snippet = first_line.strip()[:80] or "HN Who's Hiring post"
    return snippet, snippet
