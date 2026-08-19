import jobscout.fetchers.himalayas as himalayas_module
from jobscout.fetchers.himalayas import HimalayasFetcher
from jobscout.models import ApplicationChannel, ContractTypeGuess


def make_item(**overrides) -> dict:
    """Shape verified live against https://himalayas.app/jobs/api."""
    defaults = dict(
        title="Aerospace Engineer - Fully Remote | Upto $85/hr",
        excerpt="About the job Mercor connects elite talent with AI research labs.",
        companyName="mercor",
        companySlug="mercor",
        employmentType="Contractor",
        minSalary=85,
        maxSalary=85,
        salaryPeriod="hourly",
        currency="USD",
        seniority=["Mid-level"],
        locationRestrictions=["Canada"],
        timezoneRestrictions=[-8, -7],
        categories=["Aerospace-Engineering", "Aerodynamics"],
        parentCategories=["Content Creator"],
        description="<h3>About the job</h3><p>Build <strong>LLM</strong> pipelines.</p>",
        pubDate=1786618533,
        expiryDate=1791802532,
        applicationLink="https://himalayas.app/companies/mercor/jobs/aerospace-engineer-3695508396",
        guid="https://himalayas.app/companies/mercor/jobs/aerospace-engineer-3695508396",
    )
    defaults.update(overrides)
    return defaults


class FakeResponse:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("boom")

    def json(self):
        return self._payload


def test_parse_item_maps_core_fields():
    job = HimalayasFetcher()._parse_item(make_item())
    assert job.title == "Aerospace Engineer - Fully Remote | Upto $85/hr"
    assert job.company == "mercor"
    assert job.source == "himalayas"
    assert job.url == "https://himalayas.app/companies/mercor/jobs/aerospace-engineer-3695508396"
    assert job.application_channel == ApplicationChannel.URL


def test_parse_item_strips_html_from_description():
    job = HimalayasFetcher()._parse_item(make_item())
    assert "<strong>" not in job.description
    assert "LLM" in job.description


def test_parse_item_falls_back_to_excerpt_when_description_empty():
    job = HimalayasFetcher()._parse_item(make_item(description=""))
    assert "Mercor connects elite talent" in job.description


def test_employment_type_becomes_contract_type_guess():
    assert HimalayasFetcher()._parse_item(make_item()).contract_type_guess == ContractTypeGuess.FREELANCE
    full_time = HimalayasFetcher()._parse_item(make_item(employmentType="Full Time"))
    assert full_time.contract_type_guess == ContractTypeGuess.EMPLOYMENT


def test_location_restrictions_become_location_text():
    # location_text is part of filters.job_eligibility_text, so this is how
    # the source's own country restrictions reach Stage 1 — rendered with
    # "only" so they read as the restriction the field actually asserts.
    job = HimalayasFetcher()._parse_item(make_item(locationRestrictions=["United States", "Canada"]))
    assert job.location_text == "United States, Canada only"
    assert HimalayasFetcher()._parse_item(make_item(locationRestrictions=[])).location_text is None


def test_posted_date_parsed_from_unix_seconds():
    job = HimalayasFetcher()._parse_item(make_item())
    assert job.posted_date is not None
    assert job.posted_date.year == 2026
    assert job.posted_date.tzinfo is not None


def test_url_prefers_guid_over_application_link():
    # applicationLink can point at a third-party ATS; guid is the stable
    # identity, and dedup_key must not depend on where a company hosts its
    # board.
    job = HimalayasFetcher()._parse_item(
        make_item(
            applicationLink="https://jobs.lever.co/mercor/abc", guid="https://himalayas.app/companies/mercor/jobs/x"
        )
    )
    assert job.url == "https://himalayas.app/companies/mercor/jobs/x"


def test_parse_item_returns_none_without_title_or_url():
    assert HimalayasFetcher()._parse_item(make_item(title="")) is None
    assert HimalayasFetcher()._parse_item(make_item(guid="", applicationLink="")) is None


def test_salary_formatting_includes_period():
    assert HimalayasFetcher()._parse_item(make_item()).salary_text == "$85 (hourly)"
    ranged = HimalayasFetcher()._parse_item(make_item(minSalary=120000, maxSalary=180000, salaryPeriod="yearly"))
    assert ranged.salary_text == "$120,000 - $180,000 (yearly)"
    assert HimalayasFetcher()._parse_item(make_item(minSalary=0, maxSalary=0)).salary_text is None


def test_fetch_paginates_by_offset_and_stops_on_short_page(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(params["offset"])
        if params["offset"] == 0:
            return FakeResponse({"jobs": [make_item(guid=f"https://himalayas.app/jobs/{i}") for i in range(20)]})
        return FakeResponse({"jobs": [make_item(guid="https://himalayas.app/jobs/last")]})

    monkeypatch.setattr(himalayas_module.requests, "get", fake_get)
    jobs = HimalayasFetcher().fetch()
    assert calls == [0, 20]  # short second page ends paging
    assert len(jobs) == 21


def test_fetch_dedupes_repeats_across_pages(monkeypatch):
    # New postings published mid-fetch shift the offset window and can push
    # the same job onto two pages.
    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResponse({"jobs": [make_item(guid="https://himalayas.app/jobs/same")]})

    monkeypatch.setattr(himalayas_module.requests, "get", fake_get)
    assert len(HimalayasFetcher().fetch()) == 1


def test_fetch_returns_empty_on_request_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(himalayas_module.requests, "get", boom)
    assert HimalayasFetcher().fetch() == []


def test_fetch_keeps_earlier_pages_when_a_later_page_fails(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if params["offset"] == 0:
            return FakeResponse({"jobs": [make_item(guid=f"https://himalayas.app/jobs/{i}") for i in range(20)]})
        raise RuntimeError("network down")

    monkeypatch.setattr(himalayas_module.requests, "get", fake_get)
    assert len(HimalayasFetcher().fetch()) == 20


def test_fetch_survives_one_malformed_item(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResponse({"jobs": [None, make_item(guid="https://himalayas.app/jobs/ok")]})

    monkeypatch.setattr(himalayas_module.requests, "get", fake_get)
    jobs = HimalayasFetcher().fetch()
    assert len(jobs) == 1


def test_fetch_returns_empty_on_unexpected_shape(monkeypatch):
    monkeypatch.setattr(himalayas_module.requests, "get", lambda *a, **k: FakeResponse([1, 2, 3]))
    assert HimalayasFetcher().fetch() == []
