"""We Work Remotely fetcher — RSS, not JSON.

Parsed with stdlib xml.etree.ElementTree: the project has no RSS/XML
dependency (no feedparser, no lxml) and this feed is plain RSS 2.0 with
extra non-namespaced elements, which ElementTree handles directly.

Known shape (verified live): each <item> carries WWR's own <type>
("Full-Time" / "Contract"), <region>, <country>, <state>, <skills> and
<category> alongside the standard RSS fields. <type> is mapped onto
Job.contract_type_guess — see ranker.resolve_contract_type.

Two parsing quirks this handles:

1. <title> packs the company and the role into one string as
   "Company: Role" ("Grailed: Senior Backend Software Engineer"). Splitting
   it is best-effort — a role whose title legitimately contains ": " before
   the company separator would split wrong, and a title with no separator
   at all leaves the company unknown.
2. <country>, <state> and <skills> are frequently present but EMPTY rather
   than absent, so every field needs an empty-string check, not just a
   None check.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

import requests

from jobscout.fetchers.common import contract_type_from_label, parse_rfc822_datetime
from jobscout.htmlutils import strip_html
from jobscout.models import ApplicationChannel, Job

logger = logging.getLogger(__name__)

USER_AGENT = "JobScout/0.1 (personal job-search tool; contact via GitHub)"
TIMEOUT = 20

# The site-wide feed carries ~100 recent postings across all categories; the
# category feeds surface older engineering-specific roles that have already
# fallen off it. Overlap between them is expected and deduped by <guid>.
FEED_URLS = (
    "https://weworkremotely.com/remote-jobs.rss",
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
)

# Used when <title> has no "Company: Role" separator. Deliberately a fixed
# string rather than a per-job guess: dedupe._UNIQUE_ROLE_ID_SOURCES lists
# this source precisely because two jobs sharing this placeholder would
# otherwise score company_similarity 1.0 and be fuzzy-merged.
_UNKNOWN_COMPANY = "(company not stated)"


class WeWorkRemotelyFetcher:
    name = "weworkremotely"

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        seen_urls: set[str] = set()

        for feed_url in FEED_URLS:
            for item in self._fetch_feed(feed_url):
                try:
                    job = self._parse_item(item)
                except Exception:
                    logger.warning("weworkremotely: skipping malformed item in %s", feed_url, exc_info=True)
                    continue
                if job is None or job.url in seen_urls:
                    continue
                seen_urls.add(job.url)
                jobs.append(job)

        return jobs

    def _fetch_feed(self, feed_url: str) -> list[ET.Element]:
        """One dead feed must not take the others down with it."""
        try:
            response = requests.get(feed_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except Exception:
            logger.warning("weworkremotely: failed to fetch or parse %s, skipping it", feed_url, exc_info=True)
            return []
        return root.findall(".//item")

    def _parse_item(self, item: ET.Element) -> Job | None:
        def text(tag: str) -> str:
            return (item.findtext(tag) or "").strip()

        # <guid> and <link> are the same URL in practice; guid is the
        # canonical identity, link the fallback.
        url = text("guid") or text("link")
        raw_title = text("title")
        if not url or not raw_title:
            return None

        company, title = _split_company_and_title(raw_title)

        location_parts = [p for p in (text("region"), text("country"), text("state")) if p]
        tags = [t for t in (text("category"), text("skills")) if t]

        return Job(
            title=title,
            company=company,
            description=strip_html(text("description")),
            url=url,
            source=self.name,
            posted_date=parse_rfc822_datetime(text("pubDate")),
            tags=tags,
            location_text=", ".join(location_parts) or None,
            application_channel=ApplicationChannel.URL,
            source_id=url,
            contract_type_guess=contract_type_from_label(text("type")),
        )


def _split_company_and_title(raw_title: str) -> tuple[str, str]:
    """"Grailed: Senior Backend Software Engineer" -> ("Grailed", "Senior
    Backend Software Engineer"). Splits on the FIRST ": " only, so a role
    title containing its own colon keeps the rest intact. Falls back to the
    whole string as the title when there's no separator or either side is
    empty — losing the company name is better than inventing one."""
    company, separator, title = raw_title.partition(": ")
    if not separator or not company.strip() or not title.strip():
        return _UNKNOWN_COMPANY, raw_title
    return company.strip(), title.strip()
