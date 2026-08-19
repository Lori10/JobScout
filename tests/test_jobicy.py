import jobscout.fetchers.jobicy as jobicy_module
from jobscout.fetchers.jobicy import JobicyFetcher
from jobscout.models import ApplicationChannel, ContractTypeGuess


def make_item(**overrides) -> dict:
    """Shape verified live against https://jobicy.com/api/v2/remote-jobs."""
    defaults = dict(
        id=149139,
        url="https://jobicy.com/jobs/149139-principal-engineering-technology-project-manager",
        jobSlug="149139-principal-engineering-technology-project-manager",
        jobTitle="Principal Engineering Technology Project Manager",
        companyName="TE Connectivity",
        companyLogo="https://jobicy.com/data/logo.webp",
        jobIndustry=["Project &amp; Program Management"],
        jobType=["Full-Time"],
        jobGeo="USA",
        jobLevel="Senior",
        jobExcerpt="At TE, you will unleash your potential.",
        jobDescription="<p>Build <strong>LLM</strong> pipelines.</p>",
        pubDate="2026-08-13T08:40:11+00:00",
        salaryMin=161200,
        salaryMax=185000,
        salaryCurrency="USD",
        salaryPeriod="yearly",
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
    job = JobicyFetcher()._parse_item(make_item())
    assert job.title == "Principal Engineering Technology Project Manager"
    assert job.company == "TE Connectivity"
    assert job.source == "jobicy"
    assert job.url.startswith("https://jobicy.com/jobs/149139")
    assert job.source_id == "149139"
    assert job.application_channel == ApplicationChannel.URL


def test_parse_item_strips_html_from_description():
    job = JobicyFetcher()._parse_item(make_item())
    assert "<strong>" not in job.description
    assert "LLM" in job.description


def test_parse_item_falls_back_to_excerpt_when_description_empty():
    assert "unleash your potential" in JobicyFetcher()._parse_item(make_item(jobDescription="")).description


def test_html_entities_unescaped_in_tags_title_and_company():
    job = JobicyFetcher()._parse_item(
        make_item(
            jobTitle="R&amp;D Engineer", companyName="Smith &amp; Co", jobIndustry=["Project &amp; Program Management"]
        )
    )
    assert job.title == "R&D Engineer"
    assert job.company == "Smith & Co"
    assert "Project & Program Management" in job.tags


def test_job_type_becomes_contract_type_guess():
    assert JobicyFetcher()._parse_item(make_item()).contract_type_guess == ContractTypeGuess.EMPLOYMENT
    contract = JobicyFetcher()._parse_item(make_item(jobType=["Contract"]))
    assert contract.contract_type_guess == ContractTypeGuess.FREELANCE
    assert JobicyFetcher()._parse_item(make_item(jobType=[])).contract_type_guess == ContractTypeGuess.UNCLEAR


def test_job_geo_becomes_location_text():
    assert (
        JobicyFetcher()._parse_item(make_item(jobGeo="Europe,  Netherlands")).location_text
        == "Europe,  Netherlands only"
    )
    assert JobicyFetcher()._parse_item(make_item(jobGeo="")).location_text is None


def test_usa_geo_renders_into_an_existing_exclude_phrase():
    # "USA" alone matches nothing; "USA only" matches exclude_phrases.
    assert JobicyFetcher()._parse_item(make_item(jobGeo="USA")).location_text == "USA only"


def test_level_included_in_tags():
    assert "Senior" in JobicyFetcher()._parse_item(make_item()).tags


def test_posted_date_parsed_from_iso_with_offset():
    job = JobicyFetcher()._parse_item(make_item())
    assert job.posted_date is not None
    assert (job.posted_date.year, job.posted_date.month) == (2026, 8)
    assert job.posted_date.tzinfo is not None


def test_salary_formatted_with_period():
    assert JobicyFetcher()._parse_item(make_item()).salary_text == "$161,200 - $185,000 (yearly)"
    hourly = JobicyFetcher()._parse_item(make_item(salaryMin=60, salaryMax=70, salaryPeriod="hourly"))
    assert hourly.salary_text == "$60 - $70 (hourly)"
    assert JobicyFetcher()._parse_item(make_item(salaryMin=0, salaryMax=0)).salary_text is None


def test_parse_item_returns_none_without_title_or_url():
    assert JobicyFetcher()._parse_item(make_item(jobTitle="")) is None
    assert JobicyFetcher()._parse_item(make_item(url="")) is None


def test_fetch_requests_the_server_max_count(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(params)
        return FakeResponse({"jobs": [make_item()]})

    monkeypatch.setattr(jobicy_module.requests, "get", fake_get)
    assert len(JobicyFetcher().fetch()) == 1
    assert seen["count"] == 100


def test_fetch_returns_empty_on_request_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(jobicy_module.requests, "get", boom)
    assert JobicyFetcher().fetch() == []


def test_fetch_returns_empty_on_http_error(monkeypatch):
    monkeypatch.setattr(jobicy_module.requests, "get", lambda *a, **k: FakeResponse({}, status_ok=False))
    assert JobicyFetcher().fetch() == []


def test_fetch_returns_empty_on_unexpected_shape(monkeypatch):
    # Jobicy returns an object; a bare list means something changed.
    monkeypatch.setattr(jobicy_module.requests, "get", lambda *a, **k: FakeResponse([1, 2, 3]))
    assert JobicyFetcher().fetch() == []


def test_fetch_survives_one_malformed_item(monkeypatch):
    monkeypatch.setattr(
        jobicy_module.requests,
        "get",
        lambda *a, **k: FakeResponse({"jobs": [None, make_item(), make_item(jobTitle="")]}),
    )
    assert len(JobicyFetcher().fetch()) == 1
