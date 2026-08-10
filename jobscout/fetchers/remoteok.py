"""RemoteOK fetcher — https://remoteok.com/api

Known shape (verified live): returns a JSON array where index [0] is a
legal-notice object, not a job — must be skipped. Requires a real
User-Agent header or RemoteOK responds 403.
"""

from __future__ import annotations

import logging
import re

import requests

from jobscout.fetchers.common import infer_application_channel, parse_iso_datetime
from jobscout.htmlutils import strip_html
from jobscout.models import Job

logger = logging.getLogger(__name__)

API_URL = "https://remoteok.com/api"
USER_AGENT = "JobScout/0.1 (personal job-search tool; contact via GitHub)"
TIMEOUT = 20

# RemoteOK occasionally has junk/promotional entries in its own feed (e.g. a
# solo founder using the "post a job" flow to announce a product launch,
# not actually hiring). These reliably have an empty `slug` (RemoteOK
# auto-generates one for every real listing, to build its permalink) AND a
# `url`/`apply_url` that RemoteOK itself fell back to the generic listings
# page instead of a specific job permalink — both signals together are a
# strong indicator this isn't a real job, not just a sparse one.
_GENERIC_LISTING_URL_RE = re.compile(r"^https?://(www\.)?remoteok\.com/remote-jobs/?$", re.IGNORECASE)

# A second, independent spam signal: RemoteOK's own tag vocabulary includes
# both "full time" and "part time" — no single real job is both, so a
# listing tagged with both is tag-stuffing (checked live against the full
# feed: exactly matched known junk/promo entries like "Stay Hungry. Stay
# Foolish. Serve Others" and "No Open Roles Currently", zero false
# positives against legitimate heavily-tagged posts).


class NotAJobError(Exception):
    """Raised for feed entries that aren't real job postings (e.g. RemoteOK
    platform spam/promo content) so they're skipped rather than stored."""


class RemoteOKFetcher:
    name = "remoteok"

    def fetch(self) -> list[Job]:
        try:
            response = requests.get(API_URL, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            response.raise_for_status()
            data = response.json()
        except Exception:
            logger.warning("remoteok: fetch failed, skipping this source", exc_info=True)
            return []

        if not isinstance(data, list) or len(data) < 2:
            logger.warning("remoteok: unexpected response shape (%s), skipping", type(data).__name__)
            return []

        jobs: list[Job] = []
        for item in data[1:]:  # index 0 is a legal-notice row, not a job
            try:
                jobs.append(self._parse_item(item))
            except NotAJobError as exc:
                logger.info("remoteok: skipping non-job entry %r (%s)", item.get("id"), exc)
            except Exception:
                logger.warning("remoteok: skipping malformed item %r", item.get("id"), exc_info=True)
        return jobs

    def _parse_item(self, item: dict) -> Job:
        if not (item.get("slug") or "").strip() and _GENERIC_LISTING_URL_RE.match(
            (item.get("url") or item.get("apply_url") or "").strip()
        ):
            raise NotAJobError("empty slug and URL points at the generic listings page, not a specific job")

        tags = list(item.get("tags") or [])
        if "full time" in tags and "part time" in tags:
            raise NotAJobError("tagged both 'full time' and 'part time' - tag-stuffing, not a real listing")

        title = item["position"]
        company = item["company"]
        description = strip_html(item.get("description", ""))
        url = item.get("url") or item.get("apply_url") or ""
        posted_date = parse_iso_datetime(item.get("date"), assume_utc=True)
        salary_text = _format_salary(item.get("salary_min"), item.get("salary_max"))
        location_text = (item.get("location") or "").strip() or None
        source_id = str(item["id"]) if item.get("id") is not None else None
        channel = infer_application_channel(description, has_url=bool(url))

        return Job(
            title=title,
            company=company,
            description=description,
            url=url,
            source=self.name,
            posted_date=posted_date,
            salary_text=salary_text,
            tags=tags,
            location_text=location_text,
            application_channel=channel,
            source_id=source_id,
        )


def _format_salary(salary_min, salary_max) -> str | None:
    try:
        smin = int(salary_min) if salary_min else 0
        smax = int(salary_max) if salary_max else 0
    except (TypeError, ValueError):
        return None
    if not smin and not smax:
        return None
    if smin and smax and smin != smax:
        return f"${smin:,} - ${smax:,}"
    return f"${(smin or smax):,}"
