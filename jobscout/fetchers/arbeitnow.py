"""Arbeitnow fetcher — https://www.arbeitnow.com/api/job-board-api

The German/EU-market source, and the one where config.yaml's German
phrases (freiberufler, werkvertrag, remote möglich, vor Ort, nur aus
deutschland) actually get exercised — the other sources are almost entirely
English-language.

Attribution: the API's own `meta.terms` asks that it not be abused and that
callers link back to the site. Job.url is always Arbeitnow's own link.

Expect a LOW relevant yield, by design. This is a general German job board,
not a tech/remote one: measured live, ~5% of postings are remote and the
large majority are non-engineering roles, so Stage 2's relevance filter
correctly discards most of what's fetched. That is the pipeline working, not
a bug to be "fixed" by loosening the filter.

Two shape notes, both verified live against 576 real records:

1. `url` is USUALLY Arbeitnow's own job page but sometimes an external
   company link (76/176 on one page were off-site, mostly the sibling
   arbeitnow.co.uk domain, a few genuine company homepages). It is used
   as-is anyway: across 576 records its normalized form was exactly as
   unique as `slug` (559 distinct each), so it does not collapse jobs in
   practice, and the canonical Arbeitnow URL cannot be reconstructed for a
   genuinely external listing — /jobs/companies/{company}/{slug} returns
   410 for those, which would hand the user a dead link instead of a
   working one. Cross-page repeats are deduped on `slug` here rather than
   left to dedupe.py, since `slug` is the source's real identity.
2. `job_types` mixes engagement types with seniority labels
   ("berufserfahren", "Mid", "berufseinstieg") and is often empty.
   contract_type_from_label returns UNCLEAR for the seniority ones, which
   is the correct answer — they say nothing about the engagement.
"""

from __future__ import annotations

import logging

import requests

from jobscout.fetchers.common import contract_type_from_label, parse_unix_timestamp
from jobscout.htmlutils import strip_html
from jobscout.models import ApplicationChannel, Job

logger = logging.getLogger(__name__)

API_URL = "https://www.arbeitnow.com/api/job-board-api"
USER_AGENT = "JobScout/0.1 (personal job-search tool; contact via GitHub)"
TIMEOUT = 20

# Pages run ~100-176 records each and are ordered newest-first, so this is a
# "most recent several hundred postings" window. Raising it mostly buys
# older postings that the relevance filter will discard anyway.
_MAX_PAGES = 5


class ArbeitnowFetcher:
    name = "arbeitnow"

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        seen_slugs: set[str] = set()

        for page in range(1, _MAX_PAGES + 1):
            try:
                response = requests.get(
                    API_URL,
                    params={"page": page},
                    headers={"User-Agent": USER_AGENT},
                    timeout=TIMEOUT,
                )
                response.raise_for_status()
                data = response.json()
            except Exception:
                logger.warning(
                    "arbeitnow: fetch failed on page %d, keeping %d jobs so far",
                    page,
                    len(jobs),
                    exc_info=True,
                )
                break

            if not isinstance(data, dict):
                logger.warning("arbeitnow: unexpected response shape on page %d, stopping", page)
                break

            page_items = data.get("data") or []
            if not page_items:
                break

            for item in page_items:
                try:
                    job = self._parse_item(item)
                except Exception:
                    logger.warning("arbeitnow: skipping malformed item %r", (item or {}).get("slug"), exc_info=True)
                    continue
                if job is None:
                    continue
                slug = item.get("slug")
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)
                jobs.append(job)

        return jobs

    def _parse_item(self, item: dict) -> Job | None:
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        slug = (item.get("slug") or "").strip()
        if not title or not url or not slug:
            return None

        return Job(
            title=title,
            company=(item.get("company_name") or "").strip() or "Unknown",
            description=strip_html(item.get("description") or ""),
            url=url,
            source=self.name,
            posted_date=parse_unix_timestamp(item.get("created_at")),
            tags=[str(t) for t in (item.get("tags") or []) if t],
            location_text=_location_text(item),
            application_channel=ApplicationChannel.URL,
            source_id=slug,
            contract_type_guess=contract_type_from_label(item.get("job_types")),
        )


def _location_text(item: dict) -> str | None:
    """Renders Arbeitnow's structured `remote` boolean into location_text.

    Stage 1 eligibility reads text, not fields (filters.job_eligibility_text
    concatenates title + description + location_text), so a bare "Berlin"
    with remote=false would sail through as eligible — a commute-to-Berlin
    role is exactly what must not reach the eligible bucket from Albania.
    The two markers used here are already in config.yaml's vocabulary:
    "vor ort" in hybrid_onsite_phrases, "remote" in
    remote_indicator_phrases. This is translating the source's own
    structured answer into the channel the filter reads, not guessing at
    one the source didn't give.
    """
    location = (item.get("location") or "").strip()
    marker = "Remote" if item.get("remote") else "vor Ort"
    return f"{location}, {marker}" if location else marker
