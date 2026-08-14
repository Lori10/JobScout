"""Ashby/Greenhouse/Lever/Workable public job-board APIs (Phase 4: PLAN.md's
"expanding bundled multi-role HN comments"). A single HN "who is hiring"
comment often links to a company's whole careers board rather than one
specific role (e.g. https://jobs.ashbyhq.com/starbridge lists 10 open
roles) — hn_whoishiring.py uses this module to expand that one comment into
one Job per listed role via the ATS's own public, unauthenticated JSON API
(not scraping), instead of judging relevance on the comment's aggregate
text.

Adding a platform means four things, all mirrored per platform below: a
board-root regex, an entry in detect_board / _KNOWN_ATS_HOST_RE, a
_fetch_<platform> pair, and a _<platform>_posting_to_job that stamps
source_id as f"{comment_id}:{platform}:{raw_id}" — that marker is what
dedupe._has_source_assigned_role_id reads to keep several distinct roles
from one company off each other's fuzzy-merge path.

A URL with an extra path segment (e.g. .../starbridge/117e56db-...) already
names one specific role and is intentionally left untouched by detect_board
— only bare board-root links are bundles.

Workable is the odd one out: its public "widget" list API
(apply.workable.com/api/v1/widget/accounts/{slug}) has no description field
at all, unlike Ashby/Greenhouse/Lever's full descriptionPlain/content. Each
role's individual page IS the only place a description-shaped string
exists, but it's a Cloudflare-protected client-rendered SPA — the only
server-rendered text is a short, truncated SEO <meta description> tag
(~250 chars, cut off mid-sentence). _fetch_workable_snippet fetches that
best-effort (never raises; a failure just means no snippet), and
_workable_posting_to_job prepends the ORIGINAL HN comment's full text ahead
of it, so relevance/skill-match scoring for an expanded Workable role isn't
solely dependent on a thin, truncated blurb.
"""

from __future__ import annotations

import html
import logging
import re

import requests

from jobscout.fetchers.common import contract_type_from_label, extract_href_urls, parse_iso_datetime, parse_unix_timestamp
from jobscout.htmlutils import strip_html
from jobscout.models import ApplicationChannel, Job

logger = logging.getLogger(__name__)

TIMEOUT = 20

# Short timeout for the speculative redirect-resolution request in
# resolve_board() — this fires for every HN posting whose link isn't
# already a recognized board URL, so one slow/unresponsive company site
# must not meaningfully delay the whole fetch.
_RESOLVE_TIMEOUT = 6

# Large companies can list hundreds of roles (real example: a well-known
# fintech's Greenhouse board had 565 open jobs) — capping keeps one bundled
# HN comment from flooding the pipeline. Normal eligibility/relevance
# filtering still applies to whichever roles are kept.
_MAX_ROLES_PER_BOARD = 40

_ASHBY_BOARD_ROOT_RE = re.compile(r"^https?://jobs\.ashbyhq\.com/([^/?#]+)/?$", re.IGNORECASE)
_GREENHOUSE_BOARD_ROOT_RE = re.compile(r"^https?://(?:boards|job-boards)\.greenhouse\.io/([^/?#]+)/?$", re.IGNORECASE)
_LEVER_BOARD_ROOT_RE = re.compile(r"^https?://jobs\.lever\.co/([^/?#]+)/?$", re.IGNORECASE)
# Workable has two live URL shapes for the same board: the modern
# path-based apply.workable.com/{slug} and the legacy {slug}.workable.com
# subdomain (still what companies paste into HN posts — real case:
# https://sumble-inc.workable.com/). The subdomain pattern excludes "apply"
# and "www" via a negative lookahead so it doesn't itself get mistaken for
# a company slug.
_WORKABLE_PATH_BOARD_ROOT_RE = re.compile(r"^https?://apply\.workable\.com/([^/?#]+)/?$", re.IGNORECASE)
_WORKABLE_SUBDOMAIN_BOARD_ROOT_RE = re.compile(r"^https?://(?!apply\.|www\.)([^./]+)\.workable\.com/?$", re.IGNORECASE)
# Broader than the ones above (no anchoring to "nothing after the board
# slug") — matches ANY URL already hosted on a known ATS, including an
# already-specific job link. Used only to skip resolve_board's redirect
# request when it plainly can't help (a URL already on one of these hosts
# that isn't a board root is already specific, not a redirect-hiding bundle).
_KNOWN_ATS_HOST_RE = re.compile(
    r"^https?://(?:jobs\.ashbyhq\.com|(?:boards|job-boards)\.greenhouse\.io|jobs\.lever\.co|[^./]+\.workable\.com)/",
    re.IGNORECASE,
)


def detect_board(url: str | None) -> tuple[str, str] | None:
    """Returns (platform, board_slug) if `url` is a bare Ashby/Greenhouse
    board-root link, else None."""
    if not url:
        return None
    match = _ASHBY_BOARD_ROOT_RE.match(url)
    if match:
        return "ashby", match.group(1)
    match = _GREENHOUSE_BOARD_ROOT_RE.match(url)
    if match:
        return "greenhouse", match.group(1)
    match = _LEVER_BOARD_ROOT_RE.match(url)
    if match:
        return "lever", match.group(1)
    match = _WORKABLE_PATH_BOARD_ROOT_RE.match(url)
    if match:
        return "workable", match.group(1)
    match = _WORKABLE_SUBDOMAIN_BOARD_ROOT_RE.match(url)
    if match:
        return "workable", match.group(1)
    return None


def resolve_board(url: str | None) -> tuple[str, str] | None:
    """Like detect_board, but for a URL that isn't already a recognized
    board link directly. Two resolution strategies, cheapest first:

    1. Follow HTTP redirects (a company's own vanity/redirect careers URL,
       e.g. https://starbridge.ai/careers, that 30x's to
       https://jobs.ashbyhq.com/starbridge) and re-check the resolved URL.
       This is a streamed, header-only request (closed without reading the
       body) since it fires for most of a thread's ~200+ comments.
    2. If that finds nothing — a real case, not hypothetical: some
       companies serve their OWN careers page directly (a plain 200, no
       redirect at all) and merely LINK to their ATS board from it rather
       than redirecting to it. langfuse.com/careers renders its own page
       but embeds an anchor to jobs.ashbyhq.com/langfuse (which had 7 real
       open roles) — the redirect check above can never see that, since it
       never downloads the body. This second, heavier request only runs
       for links that didn't already resolve via redirect, and scans the
       page's actual HTML for an href matching a known ATS board root.

    Never raises; returns None on any failure or when neither strategy
    finds a recognized board. Callers should only invoke this when
    detect_board(url) already returned None, since it costs real HTTP
    requests. Skips both requests entirely (returns None immediately) for
    a URL already hosted on a known ATS — if detect_board() didn't match
    it, it's already a specific job link, not a redirect-hiding bundle."""
    if not url or _KNOWN_ATS_HOST_RE.match(url):
        return None

    try:
        response = requests.get(url, allow_redirects=True, timeout=_RESOLVE_TIMEOUT, stream=True)
        response.close()
    except Exception:
        logger.debug("ats_boards: could not resolve redirects for %r", url, exc_info=True)
        return None

    board = detect_board(response.url)
    if board is not None:
        return board

    try:
        response = requests.get(url, timeout=_RESOLVE_TIMEOUT)
        response.raise_for_status()
    except Exception:
        logger.debug("ats_boards: could not fetch page body for %r", url, exc_info=True)
        return None

    for href in extract_href_urls(response.text):
        board = detect_board(href)
        if board is not None:
            return board
    return None


def fetch_board_postings(platform: str, board_slug: str) -> list[dict]:
    """Never raises — an ATS API hiccup falls back to the original single
    comment-as-Job behavior (see hn_whoishiring._expand_bundled_board)."""
    if platform == "ashby":
        return _fetch_ashby(board_slug)
    if platform == "greenhouse":
        return _fetch_greenhouse(board_slug)
    if platform == "lever":
        return _fetch_lever(board_slug)
    if platform == "workable":
        return _fetch_workable(board_slug)
    return []


def _fetch_ashby(board_slug: str) -> list[dict]:
    try:
        response = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{board_slug}", timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except Exception:
        logger.warning("ats_boards: failed to fetch ashby board %r", board_slug, exc_info=True)
        return []
    postings = [j for j in (data.get("jobs") or []) if j.get("isListed", True)]
    return postings[:_MAX_ROLES_PER_BOARD]


def _fetch_greenhouse(board_slug: str) -> list[dict]:
    try:
        response = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{board_slug}/jobs",
            params={"content": "true"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        logger.warning("ats_boards: failed to fetch greenhouse board %r", board_slug, exc_info=True)
        return []
    return (data.get("jobs") or [])[:_MAX_ROLES_PER_BOARD]


def _fetch_lever(board_slug: str) -> list[dict]:
    """Unlike Ashby and Greenhouse, Lever returns a BARE JSON LIST — there
    is no {"jobs": [...]} wrapper to unwrap."""
    try:
        response = requests.get(
            f"https://api.lever.co/v0/postings/{board_slug}",
            params={"mode": "json"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        logger.warning("ats_boards: failed to fetch lever board %r", board_slug, exc_info=True)
        return []
    if not isinstance(data, list):
        logger.warning("ats_boards: unexpected lever payload for board %r", board_slug)
        return []
    return data[:_MAX_ROLES_PER_BOARD]


def _fetch_workable(board_slug: str) -> list[dict]:
    """Unlike Ashby/Greenhouse/Lever, the widget response carries a
    board-level company `name` — the ONE platform here that gives one at
    all (Ashby's top level is {jobs, apiVersion}; Lever is a bare list with
    no metadata; Greenhouse has none either). That name is more reliable
    than the HN comment's own parsed company (real case: this exact API
    call is what surfaces "Sumble Inc" for a comment whose freeform header
    had no company field to parse at all), so it's stashed onto each
    posting as a synthetic `_board_company_name` key for
    _workable_posting_to_job to prefer over the comment-parsed fallback.
    This key is JobScout's own addition, not part of Workable's response."""
    try:
        response = requests.get(f"https://apply.workable.com/api/v1/widget/accounts/{board_slug}", timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except Exception:
        logger.warning("ats_boards: failed to fetch workable board %r", board_slug, exc_info=True)
        return []
    if not isinstance(data, dict):
        logger.warning("ats_boards: unexpected workable payload for board %r", board_slug)
        return []
    company_name = (data.get("name") or "").strip() or None
    postings = [p for p in (data.get("jobs") or []) if isinstance(p, dict)][:_MAX_ROLES_PER_BOARD]
    for posting in postings:
        posting["_board_company_name"] = company_name
    return postings


_META_DESCRIPTION_RE = re.compile(r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']*)["\']', re.IGNORECASE)


def _fetch_workable_snippet(job_url: str) -> str | None:
    """Best-effort only — see the module docstring for why this exists and
    why it's this thin. Never raises; a failure or missing tag just means
    no snippet, and the caller still has the original comment text."""
    try:
        response = requests.get(job_url, timeout=_RESOLVE_TIMEOUT)
        response.raise_for_status()
    except Exception:
        logger.debug("ats_boards: could not fetch workable job page %r", job_url, exc_info=True)
        return None
    match = _META_DESCRIPTION_RE.search(response.text)
    if not match:
        return None
    return html.unescape(match.group(1)).strip() or None


def posting_to_job(
    platform: str, raw: dict, *, company: str, comment_id, fallback_posted_date, original_description: str | None = None
) -> Job | None:
    """Returns None (never raises) for a malformed posting missing a
    url/title — the caller drops it rather than storing a useless Job.
    `original_description` is only consumed by Workable (see
    _workable_posting_to_job); the other platforms already get a full
    description directly from their own API and ignore it."""
    if platform == "ashby":
        return _ashby_posting_to_job(raw, company, comment_id, fallback_posted_date)
    if platform == "greenhouse":
        return _greenhouse_posting_to_job(raw, company, comment_id, fallback_posted_date)
    if platform == "lever":
        return _lever_posting_to_job(raw, company, comment_id, fallback_posted_date)
    if platform == "workable":
        return _workable_posting_to_job(raw, company, comment_id, fallback_posted_date, original_description)
    return None


def _ashby_posting_to_job(raw: dict, company: str, comment_id, fallback_posted_date) -> Job | None:
    url = raw.get("jobUrl") or raw.get("applyUrl")
    title = raw.get("title")
    if not url or not title:
        return None
    description = raw.get("descriptionPlain") or strip_html(raw.get("descriptionHtml") or "")
    posted_date = parse_iso_datetime(raw.get("publishedAt"), assume_utc=True) or fallback_posted_date
    tags = [t for t in (raw.get("department"), raw.get("team")) if t]
    return Job(
        title=title.strip(),
        company=company,
        description=description,
        url=url,
        source="hn_whoishiring",
        posted_date=posted_date,
        tags=tags,
        location_text=raw.get("location"),
        application_channel=ApplicationChannel.URL,
        source_id=f"{comment_id}:ashby:{raw.get('id')}",
    )


def _greenhouse_posting_to_job(raw: dict, company: str, comment_id, fallback_posted_date) -> Job | None:
    url = raw.get("absolute_url")
    title = raw.get("title")
    if not url or not title:
        return None
    description = strip_html(raw.get("content") or "")
    posted_date = (
        parse_iso_datetime(raw.get("first_published"), assume_utc=True)
        or parse_iso_datetime(raw.get("updated_at"), assume_utc=True)
        or fallback_posted_date
    )
    tags = [d.get("name") for d in (raw.get("departments") or []) if d.get("name")]
    location = (raw.get("location") or {}).get("name")
    return Job(
        title=title.strip(),
        company=company,
        description=description,
        url=url,
        source="hn_whoishiring",
        posted_date=posted_date,
        tags=tags,
        location_text=location,
        application_channel=ApplicationChannel.URL,
        source_id=f"{comment_id}:greenhouse:{raw.get('id')}",
    )


def _lever_posting_to_job(raw: dict, company: str, comment_id, fallback_posted_date) -> Job | None:
    url = raw.get("hostedUrl") or raw.get("applyUrl")
    title = raw.get("text")
    if not url or not title:
        return None

    categories = raw.get("categories") or {}

    # Lever splits a posting's prose across several fields and no single one
    # is reliably present (measured on a real 81-role board: descriptionPlain
    # was missing on 4, additionalPlain on 2). `lists` holds the
    # Responsibilities/Requirements bullets — exactly the skill/keyword text
    # the ranker and the relevance filter need — so it must not be dropped.
    sections = [raw.get("descriptionPlain") or strip_html(raw.get("description") or "")]
    for entry in raw.get("lists") or []:
        heading = (entry.get("text") or "").strip()
        body = strip_html(entry.get("content") or "")
        if body:
            sections.append(f"{heading}\n{body}" if heading else body)
    sections.append(raw.get("additionalPlain") or strip_html(raw.get("additional") or ""))
    description = "\n\n".join(section for section in sections if section and section.strip())

    # workplaceType ("remote"/"hybrid"/"onsite") is Lever's own answer to the
    # question Stage 1 has to ask, and its vocabulary already matches
    # config.yaml's hybrid_onsite_phrases / remote_indicator_phrases.
    location = (categories.get("location") or "").strip()
    workplace_type = (raw.get("workplaceType") or "").strip()
    location_text = ", ".join(part for part in (location, workplace_type) if part) or None

    tags = [t for t in (categories.get("team"), categories.get("department")) if t]

    return Job(
        title=title.strip(),
        company=company,
        description=description,
        url=url,
        source="hn_whoishiring",
        # createdAt is MILLISECONDS here; Ashby/Greenhouse use ISO strings.
        posted_date=parse_unix_timestamp(raw.get("createdAt"), milliseconds=True) or fallback_posted_date,
        tags=tags,
        location_text=location_text,
        application_channel=ApplicationChannel.URL,
        source_id=f"{comment_id}:lever:{raw.get('id')}",
        contract_type_guess=contract_type_from_label(categories.get("commitment")),
    )


def _workable_posting_to_job(
    raw: dict, company: str, comment_id, fallback_posted_date, original_description: str | None = None
) -> Job | None:
    url = raw.get("url")
    title = raw.get("title")
    if not url or not title:
        return None

    resolved_company = raw.get("_board_company_name") or company

    snippet = _fetch_workable_snippet(url)
    role_lines = [f"Role: {title}"]
    if snippet:
        role_lines.append(snippet)
    sections = [s for s in (original_description, "\n".join(role_lines)) if s and s.strip()]
    description = "\n\n---\n".join(sections)

    location_text = ", ".join(part for part in (raw.get("city"), raw.get("state"), raw.get("country")) if part) or None
    tags = [t for t in (raw.get("department"),) if t]
    posted_date = (
        parse_iso_datetime(raw.get("published_on"), assume_utc=True)
        or parse_iso_datetime(raw.get("created_at"), assume_utc=True)
        or fallback_posted_date
    )

    return Job(
        title=title.strip(),
        company=resolved_company,
        description=description,
        url=url,
        source="hn_whoishiring",
        posted_date=posted_date,
        tags=tags,
        location_text=location_text,
        application_channel=ApplicationChannel.URL,
        source_id=f"{comment_id}:workable:{raw.get('shortcode')}",
        contract_type_guess=contract_type_from_label(raw.get("employment_type")),
    )
