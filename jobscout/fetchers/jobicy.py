"""Jobicy fetcher — https://jobicy.com/api/v2/remote-jobs

Attribution: Jobicy's own API response carries a `friendlyNotice` asking
that Jobicy be credited with a direct link to the source. Job.url is always
the jobicy.com posting page (never a rewritten apply link), so both the CLI
table and the dashboard link straight back to it.

Known shape (verified live): `count` is CLAMPED to 100 server-side (asking
for 200 or 500 returns 100 and echoes "appliedFilters": {"count": 100}), and
there is no offset/page parameter — so 100 most-recent postings per run is
the hard ceiling for this source, not a self-imposed cap. `jobIndustry`
values arrive HTML-escaped ("Project &amp; Program Management").
"""

from __future__ import annotations

import html as html_lib
import logging

import requests

from jobscout.fetchers.common import (
    contract_type_from_label,
    format_location_restrictions,
    format_salary_range,
    parse_iso_datetime,
)
from jobscout.htmlutils import strip_html
from jobscout.models import ApplicationChannel, Job

logger = logging.getLogger(__name__)

API_URL = "https://jobicy.com/api/v2/remote-jobs"
USER_AGENT = "JobScout/0.1 (personal job-search tool; contact via GitHub)"
TIMEOUT = 20

# Server-enforced maximum; larger values are silently clamped to this.
_MAX_COUNT = 100


class JobicyFetcher:
    name = "jobicy"

    def fetch(self) -> list[Job]:
        try:
            response = requests.get(
                API_URL,
                params={"count": _MAX_COUNT},
                headers={"User-Agent": USER_AGENT},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except Exception:
            logger.warning("jobicy: fetch failed, skipping this source", exc_info=True)
            return []

        if not isinstance(data, dict):
            logger.warning("jobicy: unexpected response shape, skipping this source")
            return []

        jobs: list[Job] = []
        for item in data.get("jobs") or []:
            try:
                job = self._parse_item(item)
            except Exception:
                logger.warning("jobicy: skipping malformed item %r", (item or {}).get("id"), exc_info=True)
                continue
            if job is not None:
                jobs.append(job)
        return jobs

    def _parse_item(self, item: dict) -> Job | None:
        title = html_lib.unescape((item.get("jobTitle") or "").strip())
        url = (item.get("url") or "").strip()
        if not title or not url:
            return None

        industries = [html_lib.unescape(str(i)) for i in (item.get("jobIndustry") or []) if i]
        level = (item.get("jobLevel") or "").strip()

        description = strip_html(item.get("jobDescription") or "") or (item.get("jobExcerpt") or "")

        return Job(
            title=title,
            company=html_lib.unescape((item.get("companyName") or "").strip()) or "Unknown",
            description=description,
            url=url,
            source=self.name,
            posted_date=parse_iso_datetime(item.get("pubDate"), assume_utc=True),
            salary_text=format_salary_range(
                item.get("salaryMin"),
                item.get("salaryMax"),
                item.get("salaryCurrency"),
                item.get("salaryPeriod"),
            ),
            tags=industries + ([level] if level else []),
            # jobGeo is a comma-separated region/country list ("Europe",
            # "USA", "Europe,  Netherlands") — the source's own statement of
            # where it will hire, which filters.job_eligibility_text reads.
            location_text=format_location_restrictions(item.get("jobGeo")),
            application_channel=ApplicationChannel.URL,
            source_id=str(item["id"]) if item.get("id") is not None else None,
            contract_type_guess=contract_type_from_label(item.get("jobType")),
        )
