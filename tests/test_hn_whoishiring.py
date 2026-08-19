from jobscout.fetchers.hn_whoishiring import HNWhoIsHiringFetcher, _parse_company_and_title
from jobscout.models import Job


def test_standard_company_role_location_format():
    first_line = (
        "Snout https://snout.com/ | Multiple Engineering + Product Roles | Remote US or Ontario, Canada | Full Time"
    )
    company, title = _parse_company_and_title(first_line)
    assert company == "Snout https://snout.com/"
    assert title == "Multiple Engineering + Product Roles"


def test_salary_before_role_skipped():
    # Real example: posters don't use a consistent field order — here
    # salary comes right after the company name, before the actual role.
    first_line = (
        "SmarterDx | 150-250k+ + equity + benefits | Remote (US only) | Multiple roles | https://smarterdx.com/careers"
    )
    company, title = _parse_company_and_title(first_line)
    assert company == "SmarterDx"
    assert title == "Multiple roles"


def test_employment_type_before_role_list_skipped():
    first_line = "PostHog | Full-Time | Technical CSMs, Technical AEs, AI Research Engineer | REMOTE (all remote)"
    company, title = _parse_company_and_title(first_line)
    assert company == "PostHog"
    assert title == "Technical CSMs, Technical AEs, AI Research Engineer"


def test_bare_url_segment_is_not_used_as_title():
    first_line = "GovStar | https://govstar.us | REMOTE — United States, Eastern or Central Time Zones | Full-time - build things"
    company, title = _parse_company_and_title(first_line)
    assert company == "GovStar"
    assert title != "https://govstar.us"
    assert not title.startswith("http")


def test_no_pipe_delimiter_falls_back_to_snippet():
    company, title = _parse_company_and_title("Just a plain sentence with no structure at all.")
    assert company == title
    assert company.startswith("Just a plain sentence")


def test_all_segments_filtered_uses_honest_placeholder_not_a_rejected_segment():
    # Real case: Foxglove's header is "Company | Onsite (SF) + Remote |
    # Full Time | url" with the actual roles listed as bullets further
    # down - no segment here is a role, so the fallback must not silently
    # reuse a location string (which is exactly the bug this replaces).
    first_line = "Acme | Remote | Full-time | https://acme.com"
    company, title = _parse_company_and_title(first_line)
    assert company == "Acme"
    assert title not in ("Remote", "Full-time", "https://acme.com")
    assert "see description" in title.lower()


def test_candidate_self_profile_is_skipped_not_parsed_as_job():
    fetcher = HNWhoIsHiringFetcher()
    comment = {
        "id": 12345,
        "text": (
            "Location: Dallas, TX<p>Remote: Yes<p>Willing to relocate: Yes (including internationally)"
            "<p>Technologies: Python, LangChain, RAG<p>Résumé&#x2F;CV: "
            '<a href="https://example.com">https://example.com</a><p>Email: me@example.com'
        ),
        "created_at": "2026-08-03T15:00:00.000Z",
    }
    assert fetcher._parse_comment(comment) is None


def test_real_company_post_with_pipe_free_body_still_parsed():
    fetcher = HNWhoIsHiringFetcher()
    comment = {
        "id": 999,
        "text": "Acme Robotics | Senior ML Engineer | Remote worldwide | Full-time<p>We build robots.",
        "created_at": "2026-08-03T15:00:00.000Z",
    }
    job = fetcher._parse_comment(comment)
    assert job is not None
    assert job.company == "Acme Robotics"
    assert job.title == "Senior ML Engineer"


def test_expand_bundled_board_replaces_ashby_board_root_with_one_job_per_role(monkeypatch):
    fetcher = HNWhoIsHiringFetcher()
    job = Job(
        title="Senior AI Engineer",
        company="Starbridge",
        description="...",
        url="https://jobs.ashbyhq.com/starbridge",
        source="hn_whoishiring",
    )
    comment = {"id": 555}

    postings = [
        {"id": "a1", "title": "AI Engineer | NYC", "jobUrl": "https://jobs.ashbyhq.com/starbridge/a1"},
        {"id": "a2", "title": "AI Engineer | EMEA/LATAM", "jobUrl": "https://jobs.ashbyhq.com/starbridge/a2"},
    ]
    monkeypatch.setattr(
        "jobscout.fetchers.hn_whoishiring.fetch_board_postings",
        lambda platform, slug: postings if (platform, slug) == ("ashby", "starbridge") else [],
    )

    expanded = fetcher._expand_bundled_board(job, comment)
    assert len(expanded) == 2
    assert {j.title for j in expanded} == {"AI Engineer | NYC", "AI Engineer | EMEA/LATAM"}
    assert all(j.company == "Starbridge" for j in expanded)
    assert all(j.source_id.startswith("555:ashby:") for j in expanded)
    assert fetcher.discovered_boards == [("ashby", "starbridge", "Starbridge", "https://jobs.ashbyhq.com/starbridge")]


def test_discovered_boards_stays_empty_when_no_board_expansion_occurs():
    fetcher = HNWhoIsHiringFetcher()
    job = Job(
        title="Senior Engineer",
        company="Acme",
        description="...",
        url="https://acme.com/careers/senior-engineer",
        source="hn_whoishiring",
    )
    expanded = fetcher._expand_bundled_board(job, {"id": 1}, resolved_cache={job.url: None})
    assert expanded == [job]
    assert fetcher.discovered_boards == []


def test_resolve_boards_concurrently_dedupes_and_populates_cache(monkeypatch):
    fetcher = HNWhoIsHiringFetcher()
    jobs = [
        Job(title="A", company="Acme", description="", url="https://acme.com/careers", source="hn_whoishiring"),
        Job(title="B", company="Beta", description="", url="https://beta.com/careers", source="hn_whoishiring"),
        # Same URL as job A, from a different comment - must be deduped, not resolved twice.
        Job(title="A2", company="Acme", description="", url="https://acme.com/careers", source="hn_whoishiring"),
        # Already a direct board link - must not be sent to resolve_board at all.
        Job(title="C", company="Gamma", description="", url="https://jobs.ashbyhq.com/gamma", source="hn_whoishiring"),
        # HN permalink fallback - must not be sent to resolve_board at all.
        Job(
            title="D",
            company="Delta",
            description="",
            url="https://news.ycombinator.com/item?id=1",
            source="hn_whoishiring",
        ),
    ]

    calls = []

    def fake_resolve(url):
        calls.append(url)
        return ("ashby", "acme") if url == "https://acme.com/careers" else None

    monkeypatch.setattr("jobscout.fetchers.hn_whoishiring.resolve_board", fake_resolve)

    cache = fetcher._resolve_boards_concurrently(jobs)
    assert sorted(calls) == ["https://acme.com/careers", "https://beta.com/careers"]
    assert cache["https://acme.com/careers"] == ("ashby", "acme")
    assert cache["https://beta.com/careers"] is None
    assert "https://jobs.ashbyhq.com/gamma" not in cache
    assert "https://news.ycombinator.com/item?id=1" not in cache


def test_expand_bundled_board_uses_resolved_cache_instead_of_calling_resolve_board(monkeypatch):
    fetcher = HNWhoIsHiringFetcher()
    job = Job(
        title="Senior AI Engineer",
        company="Starbridge",
        description="...",
        url="https://starbridge.ai/careers",
        source="hn_whoishiring",
    )

    def boom(url):
        raise AssertionError("resolve_board should not be called when a cache is provided")

    monkeypatch.setattr("jobscout.fetchers.hn_whoishiring.resolve_board", boom)
    monkeypatch.setattr(
        "jobscout.fetchers.hn_whoishiring.fetch_board_postings",
        lambda platform, slug: [
            {"id": "a1", "title": "AI Engineer", "jobUrl": "https://jobs.ashbyhq.com/starbridge/a1"}
        ],
    )

    cache = {"https://starbridge.ai/careers": ("ashby", "starbridge")}
    expanded = fetcher._expand_bundled_board(job, {"id": 1}, resolved_cache=cache)
    assert len(expanded) == 1
    assert expanded[0].title == "AI Engineer"


def test_expand_bundled_board_resolves_redirect_to_a_board(monkeypatch):
    # Real case: Starbridge's HN comment links https://starbridge.ai/careers
    # (their own domain), which redirects to https://jobs.ashbyhq.com/starbridge
    # - no Ashby URL appears anywhere in the comment text itself, so
    # detect_board() on the literal link fails and resolve_board() must
    # follow the redirect to find the board.
    fetcher = HNWhoIsHiringFetcher()
    job = Job(
        title="Senior AI Engineer",
        company="Starbridge",
        description="Apply: https://starbridge.ai/careers",
        url="https://starbridge.ai/careers",
        source="hn_whoishiring",
    )
    monkeypatch.setattr("jobscout.fetchers.hn_whoishiring.resolve_board", lambda url: ("ashby", "starbridge"))
    monkeypatch.setattr(
        "jobscout.fetchers.hn_whoishiring.fetch_board_postings",
        lambda platform, slug: [
            {"id": "a1", "title": "AI Engineer | NYC", "jobUrl": "https://jobs.ashbyhq.com/starbridge/a1"}
        ],
    )

    expanded = fetcher._expand_bundled_board(job, {"id": 1})
    assert len(expanded) == 1
    assert expanded[0].title == "AI Engineer | NYC"


def test_expand_bundled_board_does_not_try_to_resolve_the_hn_permalink_fallback(monkeypatch):
    # When no URL was found in the comment at all, job.url falls back to
    # the HN item permalink - that must never be sent through resolve_board
    # (it's not a company link, and would just waste a request).
    fetcher = HNWhoIsHiringFetcher()
    job = Job(
        title="Some Role",
        company="Acme",
        description="Email us at jobs@acme.com",
        url="https://news.ycombinator.com/item?id=42",
        source="hn_whoishiring",
    )

    def boom(url):
        raise AssertionError("resolve_board should not have been called")

    monkeypatch.setattr("jobscout.fetchers.hn_whoishiring.resolve_board", boom)
    expanded = fetcher._expand_bundled_board(job, {"id": 42})
    assert expanded == [job]


def test_expand_bundled_board_leaves_non_board_url_unchanged(monkeypatch):
    fetcher = HNWhoIsHiringFetcher()
    job = Job(
        title="Senior Backend Engineer",
        company="Acme",
        description="...",
        url="https://acme.com/careers/backend-engineer",
        source="hn_whoishiring",
    )
    # Simulates resolve_board() actually trying the redirect and finding
    # nothing - a real (mocked, not live) network attempt, not a skip.
    monkeypatch.setattr("jobscout.fetchers.hn_whoishiring.resolve_board", lambda url: None)
    expanded = fetcher._expand_bundled_board(job, {"id": 1})
    assert expanded == [job]


def test_expand_bundled_board_leaves_specific_ashby_job_link_unchanged():
    # A URL naming one specific role is not a bundle; must not be expanded.
    fetcher = HNWhoIsHiringFetcher()
    job = Job(
        title="Senior AI Engineer",
        company="Starbridge",
        description="...",
        url="https://jobs.ashbyhq.com/starbridge/117e56db-e1b7-4f83-bc18-44af88efa04e",
        source="hn_whoishiring",
    )
    expanded = fetcher._expand_bundled_board(job, {"id": 1})
    assert expanded == [job]


def test_expand_bundled_board_falls_back_to_original_job_when_api_returns_nothing(monkeypatch):
    fetcher = HNWhoIsHiringFetcher()
    job = Job(
        title="Senior AI Engineer",
        company="Starbridge",
        description="...",
        url="https://jobs.ashbyhq.com/starbridge",
        source="hn_whoishiring",
    )
    monkeypatch.setattr("jobscout.fetchers.hn_whoishiring.fetch_board_postings", lambda platform, slug: [])
    expanded = fetcher._expand_bundled_board(job, {"id": 1})
    assert expanded == [job]


def test_url_recovers_full_link_when_hn_truncates_the_display_text():
    # Real case: Tonic AI's HN comment has <a href="...b62365ff5fc2">
    # with displayed text "...b62..." (HN's own truncation for long
    # URLs) - the stored job.url must be the real, complete href, not
    # the truncated text (which 404'd when opened).
    fetcher = HNWhoIsHiringFetcher()
    comment = {
        "id": 49158072,
        "text": (
            "Tonic AI builds data infrastructure.<p>Apply here: "
            '<a href="https:&#x2F;&#x2F;jobs.ashbyhq.com&#x2F;TonicAI&#x2F;048a114d-fb5f-46ef-b0ff-b62365ff5fc2" '
            'rel="nofollow">https:&#x2F;&#x2F;jobs.ashbyhq.com&#x2F;TonicAI&#x2F;048a114d-fb5f-46ef-b0ff-b62...</a> '
            "but also shoot me an email."
        ),
        "created_at": "2026-08-03T15:00:00.000Z",
    }
    job = fetcher._parse_comment(comment)
    assert job is not None
    assert job.url == "https://jobs.ashbyhq.com/TonicAI/048a114d-fb5f-46ef-b0ff-b62365ff5fc2"


def test_url_picks_apply_link_not_an_earlier_blog_link_in_the_body():
    # Real case: Langfuse's post linked its own "why we joined ClickHouse"
    # blog post before the actual careers link, and the naive "first URL
    # in the text" approach stored the blog post as job.url/dedup_key.
    fetcher = HNWhoIsHiringFetcher()
    comment = {
        "id": 49180088,
        "text": (
            "Langfuse (now part of ClickHouse) | Product Engineers & Backend Engineers"
            "<p>Why we joined: https://langfuse.com/blog/joining-clickhouse"
            "<p>Our handbook: https://langfuse.com/handbook"
            "<p>Check out our open roles and apply to the one you think fits you best: "
            "https://langfuse.com/careers"
        ),
        "created_at": "2026-08-03T15:00:00.000Z",
    }
    job = fetcher._parse_comment(comment)
    assert job is not None
    assert job.url == "https://langfuse.com/careers"


# --- Bullet-listed role names recovering the placeholder title ---


FLYWHEEL_MOTION_BODY = (
    "Flywheel Motion ( https://flywheelmotion.com/?utm_source=hackernews ) | REMOTE (worldwide) | Contract\n"
    "We build authority infrastructure for people whose expertise has outgrown their digital presence.\n"
    "Open now:\n"
    "- Sr Agentic Engineer — Claude Code / Cursor / Aider across the FM stack: member site infrastructure.\n"
    "- AI-Native Tech & Growth Director — owns the platform and the agentic build system behind launches.\n"
    "- Agentic Operations Coordinator — the bridge between strategy and build.\n"
    "Also open: brand strategy, editorial production, PR, design and copywriting.\n"
    "All roles: https://flywheelmotion.com/en/careers/"
)

FOXGLOVE_BODY = (
    "Foxglove | Onsite (San Francisco) + Remote | Full Time | https://foxglove.dev/\n"
    "Foxglove is the leading data platform for robotics & physical AI.\n"
    "Open roles:\n"
    "- Forward-Deployed Engineer (UK / Zurich, Switzerland)\n"
    "- Staff Product Manager, Data Platform (San Francisco)\n"
    "- Account Executive, Defense & Aerospace (San Francisco or Remote)\n"
    "Email in profile (please do not email me 5 paragraphs of AI text)."
)

# Real case: a benefits/perks bullet list that reads exactly like a role
# list but has no "Open roles:"-style heading before it.
REEF_TECHNOLOGIES_BODY = (
    "Senior Python Backend Engineer | REMOTE (EMEA/APAC)\n"
    "We're looking for Python backend engineers to work on decentralized systems.\n"
    "join Reef Technologies and tackle complex technical challenges on your own terms:\n"
    "- Contribute from wherever you like; we are fully remote\n"
    "- Set your own time commitment, as long as it's at least 30h per week\n"
    "- See how we work in our handbook: github.com/reef-technologies/handbook\n"
)

# Real case: bullets exist but with no heading at all, and each is followed
# by prose + its own apply link rather than being contiguous.
POMELO_CARE_BODY = (
    "Pomelo Care| Remote (US) First with offices in NYC and SF\n"
    "- Staff Software Engineer, Patient Experience\n"
    "Tech lead for our patient mobile/web apps. Stack: Expo, React Native.\n"
    "Apply here: https://grnh.se/ws5ijibc4us\n"
    "- Senior/Staff Security Engineer\n"
    "A builder-first role focused on shipping secure-by-default tooling.\n"
    "Apply here: https://grnh.se/jm9kjyz34us\n"
)


def test_bulleted_roles_title_extracts_role_names_after_open_now_heading():
    from jobscout.fetchers.hn_whoishiring import _bulleted_roles_title

    title = _bulleted_roles_title(FLYWHEEL_MOTION_BODY)
    assert title == (
        "Multiple roles: Sr Agentic Engineer, AI-Native Tech & Growth Director, Agentic Operations Coordinator"
    )


def test_bulleted_roles_title_extracts_role_names_after_open_roles_heading():
    from jobscout.fetchers.hn_whoishiring import _bulleted_roles_title

    title = _bulleted_roles_title(FOXGLOVE_BODY)
    assert title.startswith("Multiple roles: Forward-Deployed Engineer, Staff Product Manager")


def test_bulleted_roles_title_stops_at_first_non_bullet_line():
    from jobscout.fetchers.hn_whoishiring import _extract_bulleted_role_names

    names = _extract_bulleted_role_names(FLYWHEEL_MOTION_BODY)
    assert names == ["Sr Agentic Engineer", "AI-Native Tech & Growth Director", "Agentic Operations Coordinator"]
    assert "Also open" not in " ".join(names)


def test_bulleted_roles_title_returns_none_without_a_heading():
    # Real case this guards against: a benefits/perks bullet list that
    # reads exactly like role bullets but has no "Open roles:" heading —
    # the heading anchor is what keeps this from becoming a false positive.
    from jobscout.fetchers.hn_whoishiring import _bulleted_roles_title

    assert _bulleted_roles_title(REEF_TECHNOLOGIES_BODY) is None


def test_bulleted_roles_title_returns_none_for_non_contiguous_bullets():
    # Pomelo Care has no "Open roles:" heading at all, so this is expected
    # to return None too (a different, harder case — see multi-link ATS
    # detection, not implemented here).
    from jobscout.fetchers.hn_whoishiring import _bulleted_roles_title

    assert _bulleted_roles_title(POMELO_CARE_BODY) is None


def test_bulleted_roles_title_returns_none_for_single_bullet():
    body = "Open roles:\n- Solo Engineer\nApply within."
    from jobscout.fetchers.hn_whoishiring import _bulleted_roles_title

    assert _bulleted_roles_title(body) is None


def test_bulleted_roles_title_caps_shown_count_and_notes_remainder():
    from jobscout.fetchers.hn_whoishiring import _bulleted_roles_title

    body = "Open roles:\n" + "\n".join(f"- Role {i}" for i in range(8))
    title = _bulleted_roles_title(body)
    assert title.count(",") == 4  # 5 shown
    assert title.endswith("(+3 more)")


def test_bulleted_roles_title_rejects_long_prose_bullets():
    # A bullet whose "role name" candidate is really a full sentence
    # (long, ends in a period) must not be mistaken for a role.
    from jobscout.fetchers.hn_whoishiring import _extract_bulleted_role_names

    body = (
        "Open roles:\n"
        "- Backend Engineer\n"
        "- We are looking for someone who has a very long sentence describing what we generally want to hire for here.\n"
    )
    names = _extract_bulleted_role_names(body)
    assert names == ["Backend Engineer"]


def test_parse_comment_uses_bulleted_title_only_when_header_has_no_role():
    fetcher = HNWhoIsHiringFetcher()
    comment = {"id": 1, "text": FLYWHEEL_MOTION_BODY.replace("\n", "<p>"), "created_at": "2026-08-03T15:01:43Z"}
    job = fetcher._parse_comment(comment)
    assert job.title.startswith("Multiple roles: Sr Agentic Engineer")
    assert job.company.startswith("Flywheel Motion")


def test_parse_comment_leaves_a_real_header_role_untouched():
    # The bulleted-title recovery must only fire on the exact placeholder —
    # a comment with a real role on the header line is unaffected.
    fetcher = HNWhoIsHiringFetcher()
    comment = {
        "id": 2,
        "text": "Acme | Senior LLM Engineer | Remote<p>Open roles:<p>- Role A<p>- Role B",
        "created_at": "2026-08-03T15:01:43Z",
    }
    job = fetcher._parse_comment(comment)
    assert job.title == "Senior LLM Engineer"


# --- Prose-listed roles, each with its own apply link ---


KADOA_BODY = (
    "Kadoa | Software & Web Scraping Engineers | Remote | Full-Time | https://kadoa.com\n"
    "We are building the web data layer for finance.\n"
    "Open roles:\n"
    "- Senior Software Engineer: https://www.kadoa.com/careers/senior-software-engineer\n"
    "- Web Scraping Engineer: https://www.kadoa.com/careers/web-scraping-engineer\n"
    "Reasons you'd love working with us: fully remote, flexible hours.\n"
)

STANFORD_BODY = (
    "Stanford Research Computing | Stanford, CA | Full-time | Four positions | HYBRID,ONSITE\n"
    "We operate HPC environments for researchers.\n"
    "We have four open positions:\n"
    "• Principal Storage Architect & Team Lead: Our current storage team lead is moving on. "
    "More info: https://bit.ly/44PbQnr\n"
    "• GPU System Engineer: We are looking to hire a lead sysadmin. More info: https://bit.ly/4vf7aly\n"
)

# Real case: a "leadership team" bio bullet list matches the same
# bullet+URL shape as a genuine role list, but each URL is a LinkedIn
# profile, not an application link.
BOOST_MY_SCHOOL_BODY = (
    "Boost My School | Senior or Lead Software Engineer (IC role) | Full-time | Remote, USA only\n"
    "Leadership team you'll be working with:\n"
    "- VP of Product, ex Apple, Flexport, Stanford CS ( https://www.linkedin.com/in/nathaniel-okun )\n"
    "- Director of Engineering, multiple-time Head of Eng ( https://www.linkedin.com/in/caitlinwoodward/ )\n"
)

# Real case: a job-board aggregator reposting OTHER companies' listings —
# each bullet is a DIFFERENT company (marked with markdown emphasis), not
# one company's own roles.
CAREERJUMPSHIP_BODY = (
    "CareerJumpShip | Multiple engineering + go-to-market roles from live ATS pools | Remote-first\n"
    "I run a free job board that surfaces active roles from Greenhouse / Lever / Ashby.\n"
    "- *Anduril* (16 roles) — Senior Full-Stack Software Engineer, Santa Ana, California · $6–$253K "
    "— https://www.careerjumpship.com/jobs/senior-fullstack-engineer-anduril-83a5a\n"
    "- *crunchyroll* (13 roles) — Senior Manager, Platform Development "
    "— https://www.careerjumpship.com/jobs/senior-manager-crunchyroll-99a1b\n"
)

# Real case: only ONE bullet ends up with its own genuine URL — the rest
# of the "hits" are a single shared link picked up from a trailing
# paragraph, not a per-bullet link. Below the minimum threshold.
FLYWHEEL_MOTION_BODY_FOR_LINKS = (
    "Flywheel Motion ( https://flywheelmotion.com/?utm_source=hackernews ) | REMOTE | Contract\n"
    "Open now:\n"
    "- Sr Agentic Engineer — Claude Code / Cursor / Aider across the stack.\n"
    "- AI-Native Tech & Growth Director — owns the platform.\n"
    "- Agentic Operations Coordinator — the bridge between strategy and build.\n"
    "All roles: https://flywheelmotion.com/en/careers/\n"
)


def test_extract_bulleted_role_links_kadoa():
    from jobscout.fetchers.hn_whoishiring import _extract_bulleted_role_links

    pairs = _extract_bulleted_role_links(KADOA_BODY)
    assert pairs == [
        ("Senior Software Engineer", "https://www.kadoa.com/careers/senior-software-engineer"),
        ("Web Scraping Engineer", "https://www.kadoa.com/careers/web-scraping-engineer"),
    ]


def test_extract_bulleted_role_links_finds_url_deep_in_a_long_bullet_paragraph():
    # Stanford's bullets are long single-line paragraphs with the URL only
    # appearing after a "More info:" aside — not immediately after the
    # role name like Kadoa's terser bullets.
    from jobscout.fetchers.hn_whoishiring import _extract_bulleted_role_links

    pairs = _extract_bulleted_role_links(STANFORD_BODY)
    assert pairs == [
        ("Principal Storage Architect & Team Lead", "https://bit.ly/44PbQnr"),
        ("GPU System Engineer", "https://bit.ly/4vf7aly"),
    ]


def test_extract_bulleted_role_links_url_on_next_line():
    # SafetyWing's second bullet has the URL on the line AFTER the role
    # name, not the same line.
    from jobscout.fetchers.hn_whoishiring import _extract_bulleted_role_links

    body = (
        "SafetyWing | 100% Remote | Hiring Globally\n"
        "- General Manager, Nomad Insurance: https://safetywing.pinpointhq.com/postings/261cc20f\n"
        "- Opener (3-Month Contract): $1.5K USD/mo base + commission\n"
        "https://safetywing.pinpointhq.com/postings/1634cc12\n"
    )
    pairs = _extract_bulleted_role_links(body)
    assert pairs == [
        ("General Manager, Nomad Insurance", "https://safetywing.pinpointhq.com/postings/261cc20f"),
        ("Opener", "https://safetywing.pinpointhq.com/postings/1634cc12"),
    ]


def test_extract_bulleted_role_links_rejects_linkedin_bio_bullets():
    from jobscout.fetchers.hn_whoishiring import _extract_bulleted_role_links

    assert _extract_bulleted_role_links(BOOST_MY_SCHOOL_BODY) == []


def test_extract_bulleted_role_links_rejects_markdown_emphasis_aggregator_bullets():
    from jobscout.fetchers.hn_whoishiring import _extract_bulleted_role_links

    assert _extract_bulleted_role_links(CAREERJUMPSHIP_BODY) == []


def test_extract_bulleted_role_links_only_finds_urls_within_a_bullets_own_span():
    # A URL belonging to a LATER, unrelated bullet/paragraph must never be
    # attributed to an earlier bullet that has none of its own.
    body = "- Role A\nDescription with no link here.\n- Role B: https://example.com/role-b\n"
    from jobscout.fetchers.hn_whoishiring import _extract_bulleted_role_links

    pairs = _extract_bulleted_role_links(body)
    assert pairs == [("Role B", "https://example.com/role-b")]


def test_bulleted_role_link_jobs_splits_when_threshold_met():
    from jobscout.fetchers.hn_whoishiring import _bulleted_role_link_jobs

    job = Job(
        title="placeholder",
        company="Kadoa",
        description=KADOA_BODY,
        url="https://kadoa.com/",
        source="hn_whoishiring",
        source_id="49165309",
    )
    jobs = _bulleted_role_link_jobs(job)
    assert len(jobs) == 2
    assert jobs[0].title == "Senior Software Engineer"
    assert jobs[0].url == "https://www.kadoa.com/careers/senior-software-engineer"
    assert jobs[0].company == "Kadoa"
    assert jobs[0].source_id == "49165309:bullet:0"
    assert jobs[1].source_id == "49165309:bullet:1"


def test_bulleted_role_link_jobs_returns_original_below_threshold():
    # Flywheel Motion only yields 1 genuine-looking pair (the rest is a
    # single shared trailing link) — below the minimum, so the comment
    # must stay as the single original Job, unchanged.
    from jobscout.fetchers.hn_whoishiring import _bulleted_role_link_jobs

    job = Job(
        title="Multiple roles: Sr Agentic Engineer, AI-Native Tech & Growth Director",
        company="Flywheel Motion",
        description=FLYWHEEL_MOTION_BODY_FOR_LINKS,
        url="https://flywheelmotion.com/en/careers/",
        source="hn_whoishiring",
        source_id="49156702",
    )
    result = _bulleted_role_link_jobs(job)
    assert result == [job]


def test_bulleted_role_link_jobs_returns_original_for_aggregator_comment():
    from jobscout.fetchers.hn_whoishiring import _bulleted_role_link_jobs

    job = Job(
        title="placeholder",
        company="CareerJumpShip",
        description=CAREERJUMPSHIP_BODY,
        url="https://www.careerjumpship.com/",
        source="hn_whoishiring",
        source_id="1",
    )
    assert _bulleted_role_link_jobs(job) == [job]


def test_fetch_falls_back_to_bulleted_role_links_when_no_board_found(monkeypatch):
    # End-to-end through fetch(): board expansion finds nothing (no known
    # ATS anywhere), so the bulleted-role-link split should kick in.
    fetcher = HNWhoIsHiringFetcher()
    monkeypatch.setattr(
        "jobscout.fetchers.hn_whoishiring.requests.get",
        lambda *a, **k: type(
            "R",
            (),
            {
                "raise_for_status": lambda self: None,
                "json": lambda self: {
                    "children": [
                        {
                            "id": 49165309,
                            "text": KADOA_BODY.replace("\n", "<p>"),
                            "created_at": "2026-08-05T00:00:00Z",
                        }
                    ]
                },
            },
        )(),
    )
    monkeypatch.setattr("jobscout.fetchers.hn_whoishiring.resolve_board", lambda url: None)
    fetcher._find_latest_thread_id = lambda: "1"
    jobs = fetcher.fetch()
    assert len(jobs) == 2
    assert {j.title for j in jobs} == {"Senior Software Engineer", "Web Scraping Engineer"}
    assert all(j.source_id.startswith("49165309:bullet:") for j in jobs)


def test_extract_bulleted_role_links_resolves_truncated_url_via_href():
    # Real case: G-Research's plain-text bullet showed the URL truncated
    # with "..." while the real href (from raw HTML) had the full path.
    plain_body = "- Performance Engineering Manager: https://www.gresearch.com/vacancies/performance-engineering-...\n"
    href_urls = ["https://www.gresearch.com/vacancies/performance-engineering-manager/"]
    from jobscout.fetchers.hn_whoishiring import _extract_bulleted_role_links

    pairs = _extract_bulleted_role_links(plain_body, href_urls)
    assert pairs == [
        ("Performance Engineering Manager", "https://www.gresearch.com/vacancies/performance-engineering-manager/")
    ]


def test_extract_bulleted_role_links_ignores_unrelated_hrefs():
    # An href that isn't a longer version of the matched URL (i.e. not a
    # truncation of it) must be left alone, not substituted in.
    plain_body = "- Role A: https://example.com/role-a\n"
    href_urls = ["https://example.com/completely-unrelated-page"]
    from jobscout.fetchers.hn_whoishiring import _extract_bulleted_role_links

    pairs = _extract_bulleted_role_links(plain_body, href_urls)
    assert pairs == [("Role A", "https://example.com/role-a")]
