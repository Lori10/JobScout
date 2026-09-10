from jobscout.fetchers.ats_boards import (
    detect_board,
    fetch_board_postings,
    posting_to_job,
    resolve_board,
)
from jobscout.models import ContractTypeGuess


class FakeResponse:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("boom")

    def json(self):
        return self._payload


# ---- detect_board ----


def test_detect_board_matches_ashby_board_root():
    assert detect_board("https://jobs.ashbyhq.com/starbridge") == ("ashby", "starbridge")


def test_detect_board_matches_ashby_board_root_with_trailing_slash():
    assert detect_board("https://jobs.ashbyhq.com/starbridge/") == ("ashby", "starbridge")


def test_detect_board_does_not_match_ashby_specific_job_link():
    # A URL naming one specific role is not a bundle - leave it alone.
    url = "https://jobs.ashbyhq.com/starbridge/117e56db-e1b7-4f83-bc18-44af88efa04e"
    assert detect_board(url) is None


def test_detect_board_matches_greenhouse_board_root_both_hosts():
    assert detect_board("https://boards.greenhouse.io/acme") == ("greenhouse", "acme")
    assert detect_board("https://job-boards.greenhouse.io/acme") == ("greenhouse", "acme")


def test_detect_board_does_not_match_greenhouse_specific_job_link():
    url = "https://job-boards.greenhouse.io/canopyworks/jobs/4317128009"
    assert detect_board(url) is None


def test_detect_board_returns_none_for_unrelated_url():
    assert detect_board("https://acme.com/careers") is None


def test_detect_board_returns_none_for_empty_url():
    assert detect_board(None) is None
    assert detect_board("") is None


# ---- resolve_board ----


class FakeRedirectResponse:
    def __init__(self, resolved_url):
        self.url = resolved_url

    def close(self):
        pass


def test_resolve_board_follows_redirect_to_ashby_board(monkeypatch):
    # Real case: Starbridge's HN post links https://starbridge.ai/careers,
    # which 30x's to https://jobs.ashbyhq.com/starbridge with no Ashby URL
    # ever appearing in the comment text itself.
    monkeypatch.setattr(
        "jobscout.fetchers.ats_boards.requests.get",
        lambda *a, **k: FakeRedirectResponse("https://jobs.ashbyhq.com/starbridge"),
    )
    assert resolve_board("https://starbridge.ai/careers") == ("ashby", "starbridge")


def test_resolve_board_returns_none_when_resolved_url_is_not_a_board(monkeypatch):
    monkeypatch.setattr(
        "jobscout.fetchers.ats_boards.requests.get",
        lambda *a, **k: FakeRedirectResponse("https://acme.com/about"),
    )
    assert resolve_board("https://acme.com/careers") is None


def test_resolve_board_returns_none_on_request_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("timeout")

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", boom)
    assert resolve_board("https://acme.com/careers") is None


def test_resolve_board_returns_none_for_empty_url():
    assert resolve_board(None) is None
    assert resolve_board("") is None


class FakeBodyResponse:
    def __init__(self, text, status_ok=True):
        self.text = text
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("boom")


def test_resolve_board_finds_board_link_embedded_in_page_body(monkeypatch):
    # Real case: langfuse.com/careers returns 200 with NO redirect at all —
    # it's Langfuse's own rendered careers page — but the page's HTML links
    # jobs.ashbyhq.com/langfuse (7 real open roles) from an anchor rather
    # than redirecting to it. The redirect-only check can never see this.
    page_html = '<a href="https://jobs.ashbyhq.com/langfuse">Open roles</a>'

    def fake_get(url, **kwargs):
        if kwargs.get("stream"):
            return FakeRedirectResponse(url)  # no redirect: same URL back
        return FakeBodyResponse(page_html)

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", fake_get)
    assert resolve_board("https://langfuse.com/careers") == ("ashby", "langfuse")


def test_resolve_board_body_scan_ignores_non_board_links(monkeypatch):
    page_html = '<a href="https://langfuse.com/blog/post">Blog</a> <a href="https://langfuse.com/handbook">Handbook</a>'

    def fake_get(url, **kwargs):
        return FakeRedirectResponse(url) if kwargs.get("stream") else FakeBodyResponse(page_html)

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", fake_get)
    assert resolve_board("https://acme.com/careers") is None


def test_resolve_board_body_scan_only_runs_when_redirect_check_finds_nothing(monkeypatch):
    # The cheap streamed redirect check already found a board — the second,
    # heavier full-body request must never fire.
    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs.get("stream", False))
        return FakeRedirectResponse("https://jobs.ashbyhq.com/starbridge")

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", fake_get)
    assert resolve_board("https://starbridge.ai/careers") == ("ashby", "starbridge")
    assert calls == [True]


def test_resolve_board_body_scan_failure_returns_none(monkeypatch):
    def fake_get(url, **kwargs):
        if kwargs.get("stream"):
            return FakeRedirectResponse(url)
        raise RuntimeError("network down")

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", fake_get)
    assert resolve_board("https://acme.com/careers") is None


def test_resolve_board_body_scan_handles_http_error_status(monkeypatch):
    def fake_get(url, **kwargs):
        return FakeRedirectResponse(url) if kwargs.get("stream") else FakeBodyResponse("", status_ok=False)

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", fake_get)
    assert resolve_board("https://acme.com/careers") is None


def test_resolve_board_skips_network_call_for_already_specific_ats_link(monkeypatch):
    # A URL already on jobs.ashbyhq.com/greenhouse.io that detect_board()
    # didn't match is already a specific job link, not a redirect-hiding
    # bundle - resolve_board must not waste a request on it.
    def boom(*a, **k):
        raise AssertionError("requests.get should not have been called")

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", boom)
    url = "https://jobs.ashbyhq.com/starbridge/117e56db-e1b7-4f83-bc18-44af88efa04e"
    assert resolve_board(url) is None


# ---- fetch_board_postings ----


def test_fetch_ashby_postings_filters_unlisted_and_caps_count(monkeypatch):
    jobs = [{"id": str(i), "title": f"Role {i}", "isListed": i % 2 == 0} for i in range(50)]
    monkeypatch.setattr(
        "jobscout.fetchers.ats_boards.requests.get",
        lambda *a, **k: FakeResponse({"jobs": jobs}),
    )
    postings = fetch_board_postings("ashby", "starbridge")
    assert all(p["isListed"] for p in postings)
    assert len(postings) <= 40


def test_fetch_greenhouse_postings_caps_count(monkeypatch):
    jobs = [{"id": i, "title": f"Role {i}"} for i in range(100)]
    monkeypatch.setattr(
        "jobscout.fetchers.ats_boards.requests.get",
        lambda *a, **k: FakeResponse({"jobs": jobs}),
    )
    postings = fetch_board_postings("greenhouse", "acme")
    assert len(postings) == 40


def test_fetch_board_postings_returns_empty_list_on_request_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network error")

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", boom)
    assert fetch_board_postings("ashby", "starbridge") == []
    assert fetch_board_postings("greenhouse", "acme") == []


def test_fetch_board_postings_returns_empty_list_for_unknown_platform():
    assert fetch_board_postings("bogus", "whatever") == []


# ---- posting_to_job ----


def test_ashby_posting_to_job_maps_fields():
    raw = {
        "id": "117e56db",
        "title": "AI Engineer | NYC",
        "department": "Engineering",
        "team": "Engineering",
        "location": "New York City",
        "publishedAt": "2026-06-12T15:28:09.546+00:00",
        "jobUrl": "https://jobs.ashbyhq.com/starbridge/117e56db",
        "applyUrl": "https://jobs.ashbyhq.com/starbridge/117e56db/application",
        "descriptionPlain": "We build things.",
    }
    job = posting_to_job("ashby", raw, company="Starbridge", comment_id=123, fallback_posted_date=None)
    assert job is not None
    assert job.title == "AI Engineer | NYC"
    assert job.company == "Starbridge"
    assert job.url == "https://jobs.ashbyhq.com/starbridge/117e56db"
    assert job.description == "We build things."
    assert job.location_text == "New York City"
    assert job.tags == ["Engineering", "Engineering"]
    assert job.source_id == "123:ashby:117e56db"


def test_ashby_posting_to_job_strips_html_when_plain_missing():
    raw = {
        "id": "1",
        "title": "Role",
        "jobUrl": "https://jobs.ashbyhq.com/acme/1",
        "descriptionHtml": "<p>Hello <strong>world</strong></p>",
    }
    job = posting_to_job("ashby", raw, company="Acme", comment_id=1, fallback_posted_date=None)
    assert job.description == "Hello world"


def test_ashby_posting_to_job_returns_none_without_url():
    raw = {"id": "1", "title": "Role"}
    assert posting_to_job("ashby", raw, company="Acme", comment_id=1, fallback_posted_date=None) is None


def test_ashby_posting_to_job_source_defaults_to_hn_whoishiring():
    raw = {"id": "1", "title": "Role", "jobUrl": "https://jobs.ashbyhq.com/acme/1"}
    job = posting_to_job("ashby", raw, company="Acme", comment_id=1, fallback_posted_date=None)
    assert job.source == "hn_whoishiring"


def test_ashby_posting_to_job_source_param_threaded():
    raw = {"id": "1", "title": "Role", "jobUrl": "https://jobs.ashbyhq.com/acme/1"}
    job = posting_to_job(
        "ashby", raw, company="Acme", comment_id=1, fallback_posted_date=None, source="ats_board_registry"
    )
    assert job.source == "ats_board_registry"


def test_greenhouse_posting_to_job_maps_fields():
    raw = {
        "id": 8077887,
        "title": " Senior Backend Engineer ",
        "absolute_url": "https://acme.com/jobs/8077887",
        "location": {"name": "Remote"},
        "first_published": "2026-07-22T13:15:53-04:00",
        "content": "<p>Great <em>role</em>.</p>",
        "departments": [{"name": "Engineering"}],
    }
    job = posting_to_job("greenhouse", raw, company="Acme", comment_id=42, fallback_posted_date=None)
    assert job is not None
    assert job.title == "Senior Backend Engineer"
    assert job.url == "https://acme.com/jobs/8077887"
    assert job.description == "Great role ."
    assert job.location_text == "Remote"
    assert job.tags == ["Engineering"]
    assert job.source_id == "42:greenhouse:8077887"


def test_greenhouse_posting_to_job_returns_none_without_title():
    raw = {"id": 1, "absolute_url": "https://acme.com/jobs/1", "title": ""}
    assert posting_to_job("greenhouse", raw, company="Acme", comment_id=1, fallback_posted_date=None) is None


def test_greenhouse_posting_to_job_source_defaults_to_hn_whoishiring():
    raw = {"id": 1, "absolute_url": "https://acme.com/jobs/1", "title": "Role"}
    job = posting_to_job("greenhouse", raw, company="Acme", comment_id=1, fallback_posted_date=None)
    assert job.source == "hn_whoishiring"


def test_greenhouse_posting_to_job_source_param_threaded():
    raw = {"id": 1, "absolute_url": "https://acme.com/jobs/1", "title": "Role"}
    job = posting_to_job(
        "greenhouse", raw, company="Acme", comment_id=1, fallback_posted_date=None, source="ats_board_registry"
    )
    assert job.source == "ats_board_registry"


def test_posting_to_job_returns_none_for_unknown_platform():
    assert posting_to_job("bogus", {}, company="Acme", comment_id=1, fallback_posted_date=None) is None


# ---- lever ----


def make_lever_posting(**overrides) -> dict:
    """Shape verified live against https://api.lever.co/v0/postings/matchgroup?mode=json."""
    defaults = {
        "id": "3414ba28",
        "text": "Android Engineer III",
        "categories": {
            "commitment": "Full-time",
            "department": "Hinge",
            "location": "New York, New York",
            "team": "Engineering",
        },
        "createdAt": 1779223091267,
        "descriptionPlain": "Hinge is the dating app designed to be deleted.",
        "additionalPlain": "401(k) Matching and other benefits.",
        "lists": [{"text": "Responsibilities", "content": "<li>Ship <b>Python</b> services</li>"}],
        "workplaceType": "hybrid",
        "hostedUrl": "https://jobs.lever.co/matchgroup/3414ba28",
        "applyUrl": "https://jobs.lever.co/matchgroup/3414ba28/apply",
    }
    defaults.update(overrides)
    return defaults


def test_detect_board_matches_lever_board_root():
    assert detect_board("https://jobs.lever.co/matchgroup") == ("lever", "matchgroup")
    assert detect_board("https://jobs.lever.co/matchgroup/") == ("lever", "matchgroup")


def test_detect_board_does_not_match_lever_specific_job_link():
    # Already names one role — not a bundle, so it must be left alone.
    assert detect_board("https://jobs.lever.co/matchgroup/3414ba28-35f7-45d3") is None


def test_resolve_board_skips_network_call_for_specific_lever_link(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("requests.get should not have been called")

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", boom)
    assert resolve_board("https://jobs.lever.co/matchgroup/3414ba28-35f7") is None


def test_fetch_lever_postings_reads_bare_list_and_caps_count(monkeypatch):
    # Lever returns a BARE list, unlike Ashby/Greenhouse's {"jobs": [...]}.
    postings = [make_lever_posting(id=str(i)) for i in range(50)]
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeResponse(postings))
    result = fetch_board_postings("lever", "matchgroup")
    assert len(result) == 40


def test_fetch_lever_postings_returns_empty_for_wrapped_payload(monkeypatch):
    # If Lever ever started wrapping, silently reading nothing beats
    # crashing — the caller falls back to the single-Job behavior.
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeResponse({"jobs": []}))
    assert fetch_board_postings("lever", "matchgroup") == []


def test_fetch_lever_postings_returns_empty_on_request_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", boom)
    assert fetch_board_postings("lever", "matchgroup") == []


def test_fetch_lever_postings_handles_empty_board(monkeypatch):
    # A real, valid response: api.lever.co/v0/postings/lever returns [].
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeResponse([]))
    assert fetch_board_postings("lever", "lever") == []


def test_lever_posting_to_job_maps_fields():
    job = posting_to_job(
        "lever", make_lever_posting(), company="Match Group", comment_id=123, fallback_posted_date=None
    )
    assert job is not None
    assert job.title == "Android Engineer III"
    assert job.company == "Match Group"
    assert job.url == "https://jobs.lever.co/matchgroup/3414ba28"
    assert job.tags == ["Engineering", "Hinge"]
    assert job.source_id == "123:lever:3414ba28"
    assert job.source == "hn_whoishiring"


def test_lever_posting_to_job_parses_millisecond_timestamp():
    job = posting_to_job("lever", make_lever_posting(), company="Match Group", comment_id=1, fallback_posted_date=None)
    # Read as seconds this value would land tens of thousands of years out.
    assert job.posted_date is not None and job.posted_date.year == 2026


def test_lever_posting_to_job_falls_back_when_created_at_missing():
    from datetime import datetime, timezone

    fallback = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job = posting_to_job(
        "lever", make_lever_posting(createdAt=None), company="Match Group", comment_id=1, fallback_posted_date=fallback
    )
    assert job.posted_date == fallback


def test_lever_posting_to_job_includes_list_sections_in_description():
    # `lists` holds the Responsibilities/Requirements bullets — the skill
    # text the ranker and relevance filter actually need.
    job = posting_to_job("lever", make_lever_posting(), company="Match Group", comment_id=1, fallback_posted_date=None)
    assert "Hinge is the dating app" in job.description
    assert "Responsibilities" in job.description
    assert "Ship Python services" in job.description
    assert "401(k) Matching" in job.description
    assert "<li>" not in job.description


def test_lever_posting_to_job_survives_missing_prose_fields():
    # Measured live: descriptionPlain was absent on 4 of 81 real postings.
    job = posting_to_job(
        "lever",
        make_lever_posting(descriptionPlain=None, additionalPlain=None, lists=[]),
        company="Match Group",
        comment_id=1,
        fallback_posted_date=None,
    )
    assert job is not None and job.description == ""


def test_lever_commitment_becomes_contract_type_guess():
    job = posting_to_job("lever", make_lever_posting(), company="X", comment_id=1, fallback_posted_date=None)
    assert job.contract_type_guess == ContractTypeGuess.EMPLOYMENT

    contract = make_lever_posting()
    contract["categories"] = dict(contract["categories"], commitment="Contract")
    assert (
        posting_to_job("lever", contract, company="X", comment_id=1, fallback_posted_date=None).contract_type_guess
        == ContractTypeGuess.FREELANCE
    )


def test_lever_workplace_type_reaches_location_text():
    # workplaceType's vocabulary already matches config.yaml's
    # hybrid_onsite_phrases, so Stage 1 can act on it.
    job = posting_to_job("lever", make_lever_posting(), company="X", comment_id=1, fallback_posted_date=None)
    assert job.location_text == "New York, New York, hybrid"

    remote = posting_to_job(
        "lever", make_lever_posting(workplaceType="remote"), company="X", comment_id=1, fallback_posted_date=None
    )
    assert remote.location_text.endswith("remote")


def test_lever_posting_to_job_returns_none_without_url_or_title():
    assert (
        posting_to_job(
            "lever",
            make_lever_posting(hostedUrl=None, applyUrl=None),
            company="X",
            comment_id=1,
            fallback_posted_date=None,
        )
        is None
    )
    assert (
        posting_to_job("lever", make_lever_posting(text=None), company="X", comment_id=1, fallback_posted_date=None)
        is None
    )


def test_lever_posting_to_job_source_defaults_to_hn_whoishiring():
    job = posting_to_job("lever", make_lever_posting(), company="X", comment_id=1, fallback_posted_date=None)
    assert job.source == "hn_whoishiring"


def test_lever_posting_to_job_source_param_threaded():
    job = posting_to_job(
        "lever", make_lever_posting(), company="X", comment_id=1, fallback_posted_date=None, source="ats_board_registry"
    )
    assert job.source == "ats_board_registry"


# ---- workable ----


def make_workable_posting(**overrides) -> dict:
    """Shape verified live against
    https://apply.workable.com/api/v1/widget/accounts/sumble-inc."""
    defaults = {
        "title": "Account Executive",
        "shortcode": "6E479FF65A",
        "employment_type": "",
        "department": None,
        "url": "https://apply.workable.com/j/6E479FF65A",
        "application_url": "https://apply.workable.com/j/6E479FF65A/apply",
        "published_on": "2026-07-15",
        "created_at": "2025-01-09",
        "country": "United States",
        "city": "",
        "state": "",
        "_board_company_name": "Sumble Inc",
    }
    defaults.update(overrides)
    return defaults


def test_detect_board_matches_workable_path_board_root():
    assert detect_board("https://apply.workable.com/sumble-inc") == ("workable", "sumble-inc")
    assert detect_board("https://apply.workable.com/sumble-inc/") == ("workable", "sumble-inc")


def test_detect_board_matches_workable_subdomain_board_root():
    # Real case: this exact URL was what an HN comment's apply link
    # resolved to for Sumble.
    assert detect_board("https://sumble-inc.workable.com") == ("workable", "sumble-inc")
    assert detect_board("https://sumble-inc.workable.com/") == ("workable", "sumble-inc")


def test_detect_board_does_not_match_workable_specific_job_link():
    assert detect_board("https://apply.workable.com/j/6E479FF65A") is None
    assert detect_board("https://apply.workable.com/sumble-inc/j/6E479FF65A") is None


def test_detect_board_excludes_apply_and_www_as_a_company_slug():
    # "apply.workable.com" alone (no slug after it) must not be mistaken
    # for a company subdomain board.
    assert detect_board("https://apply.workable.com/") is None
    assert detect_board("https://www.workable.com/") is None


def test_resolve_board_skips_network_call_for_specific_workable_job_link(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("requests.get should not have been called")

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", boom)
    assert resolve_board("https://apply.workable.com/j/6E479FF65A") is None


def test_fetch_workable_postings_reads_jobs_stamps_company_and_caps_count(monkeypatch):
    postings = [make_workable_posting(shortcode=str(i)) for i in range(50)]
    payload = {"name": "Sumble Inc", "description": None, "jobs": postings}
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeResponse(payload))
    result = fetch_board_postings("workable", "sumble-inc")
    assert len(result) == 40
    assert all(p["_board_company_name"] == "Sumble Inc" for p in result)


def test_fetch_workable_postings_returns_empty_for_unexpected_shape(monkeypatch):
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeResponse([1, 2, 3]))
    assert fetch_board_postings("workable", "sumble-inc") == []


def test_fetch_workable_postings_returns_empty_on_request_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", boom)
    assert fetch_board_postings("workable", "sumble-inc") == []


def test_fetch_workable_postings_handles_missing_or_null_name(monkeypatch):
    payload = {"name": None, "jobs": [make_workable_posting()]}
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeResponse(payload))
    result = fetch_board_postings("workable", "sumble-inc")
    assert result[0]["_board_company_name"] is None


def test_fetch_workable_snippet_extracts_and_unescapes_meta_description(monkeypatch):
    from jobscout.fetchers.ats_boards import _fetch_workable_snippet

    page = '<html><head><meta name="description" content="About Us: Sumble&#x27;s focus is data &amp; AI..."></head></html>'
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeBodyResponse(page))
    snippet = _fetch_workable_snippet("https://apply.workable.com/j/6E479FF65A")
    assert snippet == "About Us: Sumble's focus is data & AI..."


def test_fetch_workable_snippet_returns_none_without_meta_tag(monkeypatch):
    from jobscout.fetchers.ats_boards import _fetch_workable_snippet

    monkeypatch.setattr(
        "jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeBodyResponse("<html><head></head></html>")
    )
    assert _fetch_workable_snippet("https://apply.workable.com/j/6E479FF65A") is None


def test_fetch_workable_snippet_never_raises_on_request_failure(monkeypatch):
    from jobscout.fetchers.ats_boards import _fetch_workable_snippet

    def boom(*a, **k):
        raise RuntimeError("timeout")

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", boom)
    assert _fetch_workable_snippet("https://apply.workable.com/j/6E479FF65A") is None


def test_workable_posting_to_job_prefers_board_company_name_over_comment_company(monkeypatch):
    # Real case this exists for: the HN comment's header had no "|"
    # delimiters, so the comment-parsed "company" was a duplicated 80-char
    # snippet — Workable's own board-level name is far more trustworthy.
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeBodyResponse("<html></html>"))
    job = posting_to_job(
        "workable",
        make_workable_posting(),
        company="Sumble is the newco from the founders of Kaggle...",
        comment_id=49157182,
        fallback_posted_date=None,
    )
    assert job.company == "Sumble Inc"


def test_workable_posting_to_job_falls_back_to_comment_company_when_board_name_missing(monkeypatch):
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeBodyResponse("<html></html>"))
    job = posting_to_job(
        "workable",
        make_workable_posting(_board_company_name=None),
        company="Fallback Co",
        comment_id=1,
        fallback_posted_date=None,
    )
    assert job.company == "Fallback Co"


def test_workable_posting_to_job_prepends_original_comment_text(monkeypatch):
    page = '<meta name="description" content="Short SEO snippet.">'
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeBodyResponse(page))
    job = posting_to_job(
        "workable",
        make_workable_posting(),
        company="Sumble Inc",
        comment_id=1,
        fallback_posted_date=None,
        original_description="Sumble is the newco from the founders of Kaggle. We are hiring.",
    )
    assert job.description.startswith("Sumble is the newco from the founders of Kaggle. We are hiring.")
    assert "Role: Account Executive" in job.description
    assert "Short SEO snippet." in job.description


def test_workable_posting_to_job_degrades_gracefully_without_snippet(monkeypatch):
    # A failed/missing snippet must never lose the original comment text —
    # that's the whole point of prepending it.
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", boom)
    job = posting_to_job(
        "workable",
        make_workable_posting(),
        company="Sumble Inc",
        comment_id=1,
        fallback_posted_date=None,
        original_description="Original comment text.",
    )
    assert job is not None
    assert "Original comment text." in job.description
    assert "Role: Account Executive" in job.description


def test_workable_posting_to_job_maps_location_tags_and_contract_type(monkeypatch):
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeBodyResponse("<html></html>"))
    job = posting_to_job(
        "workable",
        make_workable_posting(country="Germany", city="Berlin", department="Engineering", employment_type="Contract"),
        company="Sumble Inc",
        comment_id=1,
        fallback_posted_date=None,
    )
    assert job.location_text == "Berlin, Germany"
    assert job.tags == ["Engineering"]
    assert job.contract_type_guess == ContractTypeGuess.FREELANCE
    assert job.source == "hn_whoishiring"
    assert job.source_id == "1:workable:6E479FF65A"


def test_workable_posting_to_job_posted_date_prefers_published_over_created(monkeypatch):
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeBodyResponse("<html></html>"))
    job = posting_to_job(
        "workable", make_workable_posting(), company="Sumble Inc", comment_id=1, fallback_posted_date=None
    )
    assert job.posted_date.year == 2026 and job.posted_date.month == 7


def test_workable_posting_to_job_falls_back_to_fallback_date_when_both_missing(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeBodyResponse("<html></html>"))
    fallback = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job = posting_to_job(
        "workable",
        make_workable_posting(published_on=None, created_at=None),
        company="Sumble Inc",
        comment_id=1,
        fallback_posted_date=fallback,
    )
    assert job.posted_date == fallback


def test_workable_posting_to_job_returns_none_without_url_or_title(monkeypatch):
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeBodyResponse("<html></html>"))
    assert (
        posting_to_job(
            "workable", make_workable_posting(url=None), company="X", comment_id=1, fallback_posted_date=None
        )
        is None
    )
    assert (
        posting_to_job(
            "workable", make_workable_posting(title=None), company="X", comment_id=1, fallback_posted_date=None
        )
        is None
    )


def test_workable_posting_to_job_source_defaults_to_hn_whoishiring(monkeypatch):
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeBodyResponse("<html></html>"))
    job = posting_to_job("workable", make_workable_posting(), company="X", comment_id=1, fallback_posted_date=None)
    assert job.source == "hn_whoishiring"


def test_workable_posting_to_job_source_param_threaded(monkeypatch):
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeBodyResponse("<html></html>"))
    job = posting_to_job(
        "workable",
        make_workable_posting(),
        company="X",
        comment_id=1,
        fallback_posted_date=None,
        source="ats_board_registry",
    )
    assert job.source == "ats_board_registry"


# ---- recruitee ----


def make_recruitee_offer(**overrides) -> dict:
    """Shape verified against Recruitee's official Careers Site API docs
    (docs.recruitee.com/reference/offers)."""
    defaults = {
        "id": 98765,
        "title": "Senior LLM Engineer",
        "description": "<p>We build <b>LLM</b> products.</p>",
        "requirements": "<ul><li>5+ years Python</li></ul>",
        "department": "Engineering",
        "tags": ["remote-friendly"],
        "locations": [{"name": "Remote - Europe"}],
        "remote": True,
        "hybrid": False,
        "on_site": False,
        "employment_type_code": "contractor",
        # Space-separated, non-ISO8601 — the format actually observed live
        # (verified against a real board), not what Recruitee's OpenAPI docs
        # imply.
        "published_at": "2026-08-01 10:00:00 UTC",
        "created_at": "2026-07-30 10:00:00 UTC",
        "careers_url": "https://acme.recruitee.com/o/senior-llm-engineer",
        "careers_apply_url": "https://acme.recruitee.com/o/senior-llm-engineer/c/new",
    }
    defaults.update(overrides)
    return defaults


def test_detect_board_matches_recruitee_board_root():
    assert detect_board("https://acme.recruitee.com") == ("recruitee", "acme")
    assert detect_board("https://acme.recruitee.com/") == ("recruitee", "acme")


def test_detect_board_does_not_match_recruitee_specific_job_link():
    assert detect_board("https://acme.recruitee.com/o/senior-llm-engineer") is None


def test_resolve_board_skips_network_call_for_specific_recruitee_link(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("requests.get should not have been called")

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", boom)
    assert resolve_board("https://acme.recruitee.com/o/senior-llm-engineer") is None


def test_fetch_recruitee_postings_reads_offers_and_caps_count(monkeypatch):
    payload = {"offers": [make_recruitee_offer(id=i) for i in range(50)]}
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeResponse(payload))
    result = fetch_board_postings("recruitee", "acme")
    assert len(result) == 40


def test_fetch_recruitee_postings_returns_empty_for_missing_offers_key(monkeypatch):
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeResponse({}))
    assert fetch_board_postings("recruitee", "acme") == []


def test_fetch_recruitee_postings_returns_empty_on_request_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", boom)
    assert fetch_board_postings("recruitee", "acme") == []


def test_recruitee_posting_to_job_maps_fields():
    job = posting_to_job("recruitee", make_recruitee_offer(), company="Acme", comment_id=1, fallback_posted_date=None)
    assert job is not None
    assert job.title == "Senior LLM Engineer"
    assert job.company == "Acme"
    assert job.url == "https://acme.recruitee.com/o/senior-llm-engineer/c/new"
    assert job.tags == ["Engineering", "remote-friendly"]
    assert job.source_id == "1:recruitee:98765"
    assert "5+ years Python" in job.description
    assert "<li>" not in job.description
    assert job.contract_type_guess == ContractTypeGuess.FREELANCE


def test_recruitee_posting_to_job_falls_back_to_careers_url_without_apply_url():
    job = posting_to_job(
        "recruitee",
        make_recruitee_offer(careers_apply_url=None),
        company="Acme",
        comment_id=1,
        fallback_posted_date=None,
    )
    assert job.url == "https://acme.recruitee.com/o/senior-llm-engineer"


def test_recruitee_posting_to_job_location_reflects_remote_flag():
    job = posting_to_job("recruitee", make_recruitee_offer(), company="Acme", comment_id=1, fallback_posted_date=None)
    assert job.location_text == "Remote - Europe, Remote"

    onsite = posting_to_job(
        "recruitee",
        make_recruitee_offer(remote=False, hybrid=False, on_site=True),
        company="Acme",
        comment_id=1,
        fallback_posted_date=None,
    )
    assert onsite.location_text.endswith("On-site")


def test_recruitee_posting_to_job_returns_none_without_url_or_title():
    assert (
        posting_to_job(
            "recruitee",
            make_recruitee_offer(careers_url=None, careers_apply_url=None),
            company="Acme",
            comment_id=1,
            fallback_posted_date=None,
        )
        is None
    )
    assert (
        posting_to_job(
            "recruitee", make_recruitee_offer(title=None), company="Acme", comment_id=1, fallback_posted_date=None
        )
        is None
    )


def test_recruitee_posting_to_job_parses_space_separated_utc_timestamp():
    # Real live shape ("2026-08-01 10:00:00 UTC"), not ISO8601 — this
    # silently dropped every Recruitee posted_date to the fallback until
    # _parse_recruitee_datetime normalized it.
    job = posting_to_job("recruitee", make_recruitee_offer(), company="Acme", comment_id=1, fallback_posted_date=None)
    assert job.posted_date is not None
    assert job.posted_date.year == 2026 and job.posted_date.month == 8 and job.posted_date.day == 1


def test_recruitee_posting_to_job_falls_back_when_dates_missing():
    from datetime import datetime, timezone

    fallback = datetime(2020, 1, 1, tzinfo=timezone.utc)
    job = posting_to_job(
        "recruitee",
        make_recruitee_offer(published_at=None, created_at=None),
        company="Acme",
        comment_id=1,
        fallback_posted_date=fallback,
    )
    assert job.posted_date == fallback


def test_recruitee_posting_to_job_source_defaults_to_hn_whoishiring():
    job = posting_to_job("recruitee", make_recruitee_offer(), company="Acme", comment_id=1, fallback_posted_date=None)
    assert job.source == "hn_whoishiring"


def test_recruitee_posting_to_job_source_param_threaded():
    job = posting_to_job(
        "recruitee",
        make_recruitee_offer(),
        company="Acme",
        comment_id=1,
        fallback_posted_date=None,
        source="ats_board_registry",
    )
    assert job.source == "ats_board_registry"


# ---- personio ----


class FakeXMLResponse:
    def __init__(self, xml_bytes, status_ok=True):
        self.content = xml_bytes
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("boom")


_PERSONIO_POSITION_XML = """
<position>
    <id>{id}</id>
    <office>{office}</office>
    <department>{department}</department>
    <recruitingCategory>{recruiting_category}</recruitingCategory>
    <name>{name}</name>
    <jobDescriptions>
        <jobDescription>
            <name>Description</name>
            <value><![CDATA[{description}]]></value>
        </jobDescription>
        <jobDescription>
            <name>Requirements</name>
            <value><![CDATA[{requirements}]]></value>
        </jobDescription>
    </jobDescriptions>
    <employmentType>{employment_type}</employmentType>
    <occupationCategory>{occupation_category}</occupationCategory>
    <createdAt>{created_at}</createdAt>
</position>
"""


def make_personio_position_xml(**overrides) -> str:
    """Shape verified against Personio's official XML feed docs
    (developer.personio.de/docs/retrieving-open-job-positions)."""
    defaults = {
        "id": 4103,
        "office": "Berlin",
        "department": "Engineering",
        "recruiting_category": "Tech",
        "name": "Senior LLM Engineer",
        "description": "<p>We build <b>LLM</b> products.</p>",
        "requirements": "5+ years Python",
        "employment_type": "contract",
        "occupation_category": "engineering",
        "created_at": "2026-07-30T10:00:00+0200",
    }
    defaults.update(overrides)
    return _PERSONIO_POSITION_XML.format(**defaults)


def make_personio_feed(*position_blocks: str) -> bytes:
    body = "\n".join(position_blocks) if position_blocks else make_personio_position_xml()
    return f"<?xml version='1.0' encoding='UTF-8'?><workzag-jobs>{body}</workzag-jobs>".encode()


def _personio_raw_posting(**overrides) -> dict:
    """The flattened-dict shape _fetch_personio produces from one <position>
    Element — what posting_to_job actually receives, never a raw Element."""
    defaults = {
        "id": "4103",
        "name": "Senior LLM Engineer",
        "office": "Berlin",
        "department": "Engineering",
        "employmentType": "contract",
        "recruitingCategory": "Tech",
        "occupationCategory": "engineering",
        "jobDescriptions": [
            {"name": "Description", "value": "<p>We build <b>LLM</b> products.</p>"},
            {"name": "Requirements", "value": "5+ years Python"},
        ],
        "createdAt": "2026-07-30T10:00:00+0200",
        "_personio_host": "acme.jobs.personio.de",
    }
    defaults.update(overrides)
    return defaults


def test_detect_board_matches_personio_board_root():
    assert detect_board("https://acme.jobs.personio.de") == ("personio", "acme.de")
    assert detect_board("https://acme.jobs.personio.com/") == ("personio", "acme.com")


def test_detect_board_does_not_match_personio_specific_job_link():
    assert detect_board("https://acme.jobs.personio.de/job/4103") is None


def test_resolve_board_skips_network_call_for_specific_personio_link(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("requests.get should not have been called")

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", boom)
    assert resolve_board("https://acme.jobs.personio.de/job/4103") is None


def test_fetch_personio_postings_reads_positions_and_caps_count(monkeypatch):
    feed = make_personio_feed(*(make_personio_position_xml(id=i) for i in range(50)))
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeXMLResponse(feed))
    result = fetch_board_postings("personio", "acme.de")
    assert len(result) == 40
    assert result[0]["_personio_host"] == "acme.jobs.personio.de"


def test_fetch_personio_postings_returns_empty_on_request_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", boom)
    assert fetch_board_postings("personio", "acme.de") == []


def test_fetch_personio_postings_handles_empty_board(monkeypatch):
    empty_feed = b"<?xml version='1.0' encoding='UTF-8'?><workzag-jobs></workzag-jobs>"
    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", lambda *a, **k: FakeXMLResponse(empty_feed))
    assert fetch_board_postings("personio", "acme.de") == []


def test_fetch_personio_postings_returns_empty_for_malformed_board_slug(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("requests.get should not have been called")

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", boom)
    assert fetch_board_postings("personio", "acme") == []


def test_fetch_personio_postings_uses_encoded_tld(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        return FakeXMLResponse(make_personio_feed())

    monkeypatch.setattr("jobscout.fetchers.ats_boards.requests.get", fake_get)
    fetch_board_postings("personio", "acme.com")
    assert captured["url"] == "https://acme.jobs.personio.com/xml"


def test_personio_posting_to_job_maps_fields():
    job = posting_to_job("personio", _personio_raw_posting(), company="Acme", comment_id=1, fallback_posted_date=None)
    assert job is not None
    assert job.title == "Senior LLM Engineer"
    assert job.company == "Acme"
    assert job.url == "https://acme.jobs.personio.de/job/4103"
    assert job.location_text == "Berlin"
    assert job.tags == ["Engineering", "Tech", "engineering"]
    assert job.source_id == "1:personio:4103"
    assert "5+ years Python" in job.description
    assert "<b>" not in job.description
    assert job.contract_type_guess == ContractTypeGuess.FREELANCE


def test_personio_posting_to_job_returns_none_without_host_id_or_title():
    missing_host = _personio_raw_posting(_personio_host=None)
    assert posting_to_job("personio", missing_host, company="Acme", comment_id=1, fallback_posted_date=None) is None

    missing_id = _personio_raw_posting(id=None)
    assert posting_to_job("personio", missing_id, company="Acme", comment_id=1, fallback_posted_date=None) is None

    missing_title = _personio_raw_posting(name=None)
    assert posting_to_job("personio", missing_title, company="Acme", comment_id=1, fallback_posted_date=None) is None


def test_personio_posting_to_job_source_defaults_to_hn_whoishiring():
    job = posting_to_job("personio", _personio_raw_posting(), company="Acme", comment_id=1, fallback_posted_date=None)
    assert job.source == "hn_whoishiring"


def test_personio_posting_to_job_source_param_threaded():
    job = posting_to_job(
        "personio",
        _personio_raw_posting(),
        company="Acme",
        comment_id=1,
        fallback_posted_date=None,
        source="ats_board_registry",
    )
    assert job.source == "ats_board_registry"
