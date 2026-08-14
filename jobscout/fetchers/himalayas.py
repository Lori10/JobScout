"""Himalayas fetcher — https://himalayas.app/jobs/api

The best-structured of the Phase 4 sources: every posting carries its own
`employmentType` ("Contractor" / "Full Time"), `locationRestrictions` and
`seniority` as real fields rather than prose to be inferred from. The
employment type is mapped straight onto Job.contract_type_guess (see
ranker.resolve_contract_type for why that beats the description scan), and
`locationRestrictions` lands in location_text, which filters.
job_eligibility_text already reads — so Stage 1 gets the source's own
country restrictions for free.

Known shape (verified live): the API CLAMPS `limit` to 20 server-side no
matter what you request (limit=100 returns 20 and echoes "limit": 20), so
paging steps by 20 via `offset`. Results are sorted newest-first, which is
what makes a page cap a sane "recent postings" window rather than an
arbitrary slice — totalCount is ~99,000, far past anything worth fetching.
"""

from __future__ import annotations

import logging

import requests

from jobscout.fetchers.common import (
    contract_type_from_label,
    format_location_restrictions,
    format_salary_range,
    parse_unix_timestamp,
)
from jobscout.htmlutils import strip_html
from jobscout.models import ApplicationChannel, Job

logger = logging.getLogger(__name__)

API_URL = "https://himalayas.app/jobs/api"
USER_AGENT = "JobScout/0.1 (personal job-search tool; contact via GitHub)"
TIMEOUT = 20

# Server-enforced; requesting more is silently clamped to this.
_PAGE_SIZE = 20
# Results are newest-first, so this is a "most recent ~300 postings" window.
# Everything fetched still goes through the normal eligibility/relevance
# filtering, so a bigger number mostly buys older, less relevant postings.
_MAX_PAGES = 15


class HimalayasFetcher:
    name = "himalayas"

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        seen_guids: set[str] = set()

        for page in range(_MAX_PAGES):
            offset = page * _PAGE_SIZE
            try:
                response = requests.get(
                    API_URL,
                    params={"limit": _PAGE_SIZE, "offset": offset},
                    headers={"User-Agent": USER_AGENT},
                    timeout=TIMEOUT,
                )
                response.raise_for_status()
                data = response.json()
            except Exception:
                logger.warning("himalayas: fetch failed at offset %d, keeping %d jobs so far", offset, len(jobs), exc_info=True)
                break

            if not isinstance(data, dict):
                logger.warning("himalayas: unexpected response shape at offset %d, stopping", offset)
                break

            page_items = data.get("jobs") or []
            if not page_items:
                break

            for item in page_items:
                try:
                    job = self._parse_item(item)
                except Exception:
                    logger.warning("himalayas: skipping malformed item %r", (item or {}).get("guid"), exc_info=True)
                    continue
                if job is None:
                    continue
                # The API can repeat a posting across pages when new jobs are
                # published mid-fetch and shift the offset window.
                if job.url in seen_guids:
                    continue
                seen_guids.add(job.url)
                jobs.append(job)

            if len(page_items) < _PAGE_SIZE:
                break

        return jobs

    def _parse_item(self, item: dict) -> Job | None:
        title = (item.get("title") or "").strip()
        # `guid` and `applicationLink` are both the himalayas.app job page in
        # practice, but guid is the stable identity — applicationLink can be
        # a third-party ATS URL, which would make dedup_key depend on where
        # the company happens to host its board.
        url = (item.get("guid") or item.get("applicationLink") or "").strip()
        if not title or not url:
            return None

        location_restrictions = [str(loc) for loc in (item.get("locationRestrictions") or []) if loc]
        seniority = [str(s) for s in (item.get("seniority") or []) if s]
        categories = [str(c) for c in (item.get("categories") or []) if c]

        description = strip_html(item.get("description") or "") or (item.get("excerpt") or "")

        return Job(
            title=title,
            company=(item.get("companyName") or "").strip() or "Unknown",
            description=description,
            url=url,
            source=self.name,
            posted_date=parse_unix_timestamp(item.get("pubDate")),
            salary_text=format_salary_range(
                item.get("minSalary"),
                item.get("maxSalary"),
                item.get("currency"),
                item.get("salaryPeriod"),
            ),
            tags=categories + seniority,
            location_text=format_location_restrictions(location_restrictions),
            application_channel=ApplicationChannel.URL,
            source_id=url,
            contract_type_guess=contract_type_from_label(item.get("employmentType")),
        )
