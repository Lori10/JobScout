from jobscout.fetchers.hn_whoishiring import HNWhoIsHiringFetcher, _parse_company_and_title
from jobscout.models import Job


def test_standard_company_role_location_format():
    first_line = "Snout https://snout.com/ | Multiple Engineering + Product Roles | Remote US or Ontario, Canada | Full Time"
    company, title = _parse_company_and_title(first_line)
    assert company == "Snout https://snout.com/"
    assert title == "Multiple Engineering + Product Roles"


def test_salary_before_role_skipped():
    # Real example: posters don't use a consistent field order — here
    # salary comes right after the company name, before the actual role.
    first_line = "SmarterDx | 150-250k+ + equity + benefits | Remote (US only) | Multiple roles | https://smarterdx.com/careers"
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
        lambda platform, slug: [{"id": "a1", "title": "AI Engineer", "jobUrl": "https://jobs.ashbyhq.com/starbridge/a1"}],
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
        lambda platform, slug: [{"id": "a1", "title": "AI Engineer | NYC", "jobUrl": "https://jobs.ashbyhq.com/starbridge/a1"}],
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