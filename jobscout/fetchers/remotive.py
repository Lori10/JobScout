"""Remotive fetcher — https://remotive.com/api/remote-jobs?category=software-dev

Known shape (verified live): returns a JSON OBJECT, not an array — the job
list is under the "jobs" key. Phase 1 only polls the software-dev category
(see README known limitations).
"""

from __future__ import annotations

import logging

import requests

from jobscout.fetchers.common import infer_application_channel, parse_iso_datetime
from jobscout.htmlutils import strip_html
from jobscout.models import Job

logger = logging.getLogger(__name__)

API_URL = "https://remotive.com/api/remote-jobs?category=software-dev"
TIMEOUT = 20


class RemotiveFetcher:
    name = "remotive"

    def fetch(self) -> list[Job]:
        try:
            response = requests.get(API_URL, timeout=TIMEOUT)
            response.raise_for_status()
            data = response.json()
        except Exception:
            logger.warning("remotive: fetch failed, skipping this source", exc_info=True)
            return []

        if not isinstance(data, dict):
            logger.warning("remotive: unexpected response shape (%s), skipping", type(data).__name__)
            return []

        jobs: list[Job] = []
        for item in data.get("jobs", []):
            try:
                jobs.append(self._parse_item(item))
            except Exception:
                logger.warning("remotive: skipping malformed item %r", item.get("id"), exc_info=True)
        return jobs

    def _parse_item(self, item: dict) -> Job:
        title = item["title"]
        company = item["company_name"]
        description = strip_html(item.get("description", ""))
        url = item["url"]
        posted_date = parse_iso_datetime(item.get("publication_date"), assume_utc=True)
        salary_text = (item.get("salary") or "").strip() or None
        tags = list(item.get("tags") or [])
        location_text = (item.get("candidate_required_location") or "").strip() or None
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
