from jobscout.fetchers.hn_whoishiring import HNWhoIsHiringFetcher, _parse_company_and_title


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