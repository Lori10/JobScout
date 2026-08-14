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
from urllib.parse import urlsplit

import requests

from jobscout.fetchers.ats_boards import detect_board, fetch_board_postings, posting_to_job, resolve_board
from jobscout.fetchers.common import URL_RE, extract_apply_url, extract_email, extract_href_urls, parse_iso_datetime
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

    def __init__(self) -> None:
        # (platform, board_slug, company, discovered_url) for every board
        # _expand_bundled_board successfully resolves this run, even one
        # that happens to return zero postings right now — still worth
        # tracking for board_registry.py to poll again in a future run.
        # pipeline.py reads this via getattr() after fetch() returns (not
        # every Fetcher has this attribute) and records it into the
        # known_boards table.
        self.discovered_boards: list[tuple[str, str, str | None, str | None]] = []

    # --- Per-thread hooks -------------------------------------------------
    # Everything below this class that varies between HN's monthly threads
    # is reachable through these three; hn_freelancer.py subclasses and
    # overrides them, inheriting fetch(), the concurrent board resolution
    # and the ATS expansion unchanged.
    search_query = '"Ask HN: Who is hiring"'

    def _matches_thread_title(self, title: str) -> bool:
        """`title` is already lowercased. Must exclude the sibling monthly
        threads, which the same search happily returns."""
        return "who is hiring" in title and "wants to be hired" not in title and "freelancer" not in title

    def _accept_comment(self, plain: str) -> bool:
        """True if this comment is someone HIRING rather than someone
        advertising themselves. Some top-level comments here are job
        SEEKERS cross-posting from the "Who wants to be hired?" thread."""
        return not _CANDIDATE_PROFILE_RE.search(plain[:600])

    def _header_line(self, plain: str) -> str:
        """The line company/title parsing runs on."""
        return plain.splitlines()[0]

    # ----------------------------------------------------------------------

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
            expanded = self._expand_bundled_board(job, comment, resolved_cache)
            if len(expanded) == 1 and expanded[0] is job:
                # No shared board link to expand — try the other shape:
                # each bulleted role carrying its OWN distinct URL right in
                # the text (see _bulleted_role_link_jobs). Only attempted
                # when board expansion found nothing, since a real ATS
                # API's per-role data is richer than what can be recovered
                # from the comment's own prose.
                expanded = _bulleted_role_link_jobs(job, extract_href_urls(comment.get("text")))
            jobs.extend(expanded)
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
        self.discovered_boards.append((platform, slug, job.company, job.url))
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
                original_description=job.description,
            )
            for raw in postings
        ]
        expanded = [j for j in expanded if j is not None]
        return expanded or [job]

    def _find_latest_thread_id(self) -> str | None:
        try:
            response = requests.get(
                SEARCH_URL,
                params={"query": self.search_query, "tags": "story"},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except Exception:
            logger.warning("%s: search request failed", self.name, exc_info=True)
            return None

        for hit in data.get("hits", []):
            if self._matches_thread_title((hit.get("title") or "").lower()):
                return hit.get("objectID")
        return None

    def _parse_comment(self, comment: dict) -> Job | None:
        if comment.get("deleted") or comment.get("dead") or not comment.get("text"):
            return None

        plain = strip_html(comment["text"])
        if not plain:
            return None
        if not self._accept_comment(plain):
            return None

        company, title = _parse_company_and_title(self._header_line(plain))
        if title == _ROLE_NOT_STATED_TITLE:
            better_title = _bulleted_roles_title(plain)
            if better_title:
                title = better_title

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


_ROLE_NOT_STATED_TITLE = "Role not stated in header — see description"


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
    # _parse_comment tries _bulleted_roles_title() as a recovery step
    # whenever this exact placeholder comes back.
    return company, _ROLE_NOT_STATED_TITLE


# Some comments list several role names as body bullets under an "Open
# roles:"/"Open now:" heading instead of naming one role on the header
# line (real cases: Flywheel Motion "Open now:\n- Sr Agentic Engineer —
# ...", Foxglove "Open roles:\n- Forward-Deployed Engineer (UK / Zurich,
# Switzerland)"). Anchoring to that heading and only taking the CONTIGUOUS
# run of bullets right after it is deliberate, not incidental — a bare
# "line starts with -" scan produces false positives: a real comment
# (Reef Technologies) has an unrelated perks/benefits list ("- Contribute
# from wherever you like; we are fully remote") that reads exactly like a
# bullet line but isn't a role name, and critically has no such heading
# before it, so the heading anchor correctly excludes it.
_OPEN_ROLES_HEADING_RE = re.compile(
    r"(?im)^[ \t]*(open (?:now|roles?|positions?)|we'?re hiring|current openings?)[ \t]*:?[ \t]*$"
)
_BULLET_LINE_RE = re.compile(r"^[ \t]*[-•–][ \t]+(.+)$")
_ROLE_NAME_SEPARATOR_RE = re.compile(r"\s+[—–]\s+")
_MAX_ROLE_NAME_CHARS = 80
_MAX_ROLE_NAME_WORDS = 12
_MAX_ROLES_IN_TITLE = 5


def _extract_bulleted_role_names(plain_body: str) -> list[str]:
    heading_match = _OPEN_ROLES_HEADING_RE.search(plain_body)
    if heading_match is None:
        return []

    names: list[str] = []
    for line in plain_body[heading_match.end() :].splitlines():
        bullet_match = _BULLET_LINE_RE.match(line)
        if bullet_match is None:
            if line.strip() == "":
                continue
            break  # first non-bullet, non-blank line ends the contiguous run
        # "Role — description" and "Role (location)" are the two observed
        # shapes; a bullet with neither separator is kept whole.
        text = bullet_match.group(1).strip()
        name = _ROLE_NAME_SEPARATOR_RE.split(text, maxsplit=1)[0]
        if "(" in name:
            name = name.split("(", 1)[0]
        name = name.strip(" -:")
        if not name or name.endswith(".") or len(name) > _MAX_ROLE_NAME_CHARS:
            continue
        if len(name.split()) > _MAX_ROLE_NAME_WORDS:
            continue
        names.append(name)
    return names


def _bulleted_roles_title(plain_body: str) -> str | None:
    """None unless at least 2 plausible role names were found — a single
    bullet is too weak a signal to trust over the honest placeholder."""
    names = _extract_bulleted_role_names(plain_body)
    if len(names) < 2:
        return None
    shown = names[:_MAX_ROLES_IN_TITLE]
    title = "Multiple roles: " + ", ".join(shown)
    if len(names) > _MAX_ROLES_IN_TITLE:
        title += f" (+{len(names) - _MAX_ROLES_IN_TITLE} more)"
    return title[:200]


# --- Prose-listed roles, each carrying its own apply link ---
#
# A different, more tractable shape than the Ashby/Greenhouse/Lever/
# Workable board expansion in ats_boards.py: instead of one shared
# board-root link, the poster gives each bulleted role its OWN distinct
# URL directly in the comment — nothing needs to be fetched or resolved,
# since everything required is already in text JobScout has. Measured
# live across 182 real "who is hiring" base comments (the ones not
# already ATS-board-expanded): 8 used this shape — Kadoa, Mitte.ai,
# G-Research, Stanford Research Computing, LiveMap, Preferred Networks,
# SafetyWing, SwingVision — spanning self-hosted careers pages, generic
# shortlink/form services (bit.ly, tally.so), and ATS platforms not
# otherwise supported here (Talentio, Pinpoint, Deel). No per-platform
# integration is needed because detection works on the comment's own
# structure, not the target URL's domain.
_ROLE_NAME_SEPARATOR_RE = re.compile(r"\s+[—–]\s+|\s+-\s+|:\s+")
_MIN_BULLETED_ROLE_LINKS = 2
_MAX_BULLETED_ROLE_LINKS = 20

# LinkedIn/Twitter/GitHub links found right next to a bullet are virtually
# always a PERSON's profile, not an application link — real case: a
# "Leadership team" bullet list ("- VP of Product, ex Apple (linkedin...)")
# matches the exact same bullet+URL shape as a genuine role list.
_NON_APPLICATION_LINK_DOMAINS = ("linkedin.com", "twitter.com", "x.com", "github.com")


def _is_application_link(url: str) -> bool:
    netloc = urlsplit(url).netloc.lower()
    return not any(netloc == d or netloc.endswith("." + d) for d in _NON_APPLICATION_LINK_DOMAINS)


def _split_role_name(text: str) -> str | None:
    match = _ROLE_NAME_SEPARATOR_RE.search(text)
    name = text[: match.start()] if match else text
    if "(" in name:
        name = name.split("(", 1)[0]
    name = name.strip()
    if len(name) >= 2 and name.startswith("*") and name.endswith("*"):
        # Markdown emphasis around just an entity name is how the one
        # aggregator-style comment found in real data marks each bullet
        # (a job board reposting *Company* — role — ... for several
        # DIFFERENT companies' listings, not one company's own roles) — a
        # real job title was never written this way in the corpus audited.
        return None
    name = name.strip(" -:")
    if not name or name.endswith(".") or len(name) > _MAX_ROLE_NAME_CHARS:
        return None
    if len(name.split()) > _MAX_ROLE_NAME_WORDS:
        return None
    return name


def _extract_bulleted_role_links(plain_body: str, href_urls: list[str] | None = None) -> list[tuple[str, str]]:
    """Returns (role_name, url) pairs for bullets that have both a
    plausible role name and their own application URL. Only the
    CONTIGUOUS span from one bullet up to the next (or end of text) is
    searched for that URL, so a URL belonging to a later, unrelated
    paragraph is never misattributed to an earlier bullet.

    `href_urls` (see extract_href_urls) recovers the real URL when HN's
    own display has visually truncated it with "..." in the plain text —
    real case: G-Research's comment showed
    "https://www.gresearch.com/vacancies/performance-engineering-..." in
    plain text, but the actual href was the complete "...manager/". Same
    truncation extract_apply_url already has to work around."""
    lines = plain_body.splitlines()
    bullet_starts = [i for i, line in enumerate(lines) if _BULLET_LINE_RE.match(line)]

    pairs: list[tuple[str, str]] = []
    for position, start in enumerate(bullet_starts):
        end = bullet_starts[position + 1] if position + 1 < len(bullet_starts) else len(lines)
        span = "\n".join(lines[start:end])
        bullet_text = _BULLET_LINE_RE.match(lines[start]).group(1)

        name = _split_role_name(bullet_text)
        if name is None:
            continue

        url_match = URL_RE.search(span)
        if url_match is None:
            continue
        url = url_match.group(0).rstrip(".,;:")
        for href in href_urls or ():
            if href.startswith(url) and len(href) > len(url):
                url = href
                break
        if not _is_application_link(url):
            continue

        pairs.append((name, url))
    return pairs[:_MAX_BULLETED_ROLE_LINKS]


def _bulleted_role_link_jobs(job: Job, href_urls: list[str] | None = None) -> list[Job]:
    """Splits `job` into one Job per bulleted (role name, URL) pair found
    in its description, reusing job's company/source metadata for all of
    them. Returns [job] unchanged (never loses the posting) when fewer
    than _MIN_BULLETED_ROLE_LINKS pairs are found — same "must never lose
    a posting outright" contract _expand_bundled_board follows."""
    pairs = _extract_bulleted_role_links(job.description, href_urls)
    if len(pairs) < _MIN_BULLETED_ROLE_LINKS:
        return [job]

    return [
        Job(
            title=name,
            company=job.company,
            description=job.description,
            url=url,
            source=job.source,
            posted_date=job.posted_date,
            tags=job.tags,
            location_text=job.location_text,
            application_channel=ApplicationChannel.URL,
            source_id=f"{job.source_id}:bullet:{index}",
        )
        for index, (name, url) in enumerate(pairs)
    ]
