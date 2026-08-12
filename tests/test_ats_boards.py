from jobscout.fetchers.ats_boards import (
    detect_board,
    fetch_board_postings,
    posting_to_job,
    resolve_board,
)


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


def test_posting_to_job_returns_none_for_unknown_platform():
    assert posting_to_job("bogus", {}, company="Acme", comment_id=1, fallback_posted_date=None) is None
