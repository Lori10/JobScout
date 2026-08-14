import jobscout.fetchers.arbeitnow as arbeitnow_module
from jobscout.config import FilterConfig
from jobscout.fetchers.arbeitnow import ArbeitnowFetcher
from jobscout.filters import apply_eligibility_filter
from jobscout.models import ApplicationChannel, ContractTypeGuess, EligibilityBucket


def make_item(**overrides) -> dict:
    """Shape verified live against https://www.arbeitnow.com/api/job-board-api."""
    defaults = dict(
        slug="software-engineer-price-comparison-portal-berlin-233707",
        company_name="Preiswecker",
        title="Software Engineer",
        description="<p>Build <strong>LLM</strong> pipelines every day.</p>",
        remote=False,
        url="https://www.arbeitnow.com/jobs/companies/preiswecker/software-engineer-berlin-233707",
        tags=["Software Engineering", "E-Commerce"],
        job_types=["Full-time"],
        location="Berlin",
        created_at=1786516800,
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
    job = ArbeitnowFetcher()._parse_item(make_item())
    assert job.title == "Software Engineer"
    assert job.company == "Preiswecker"
    assert job.source == "arbeitnow"
    assert job.source_id == "software-engineer-price-comparison-portal-berlin-233707"
    assert job.tags == ["Software Engineering", "E-Commerce"]
    assert job.application_channel == ApplicationChannel.URL


def test_description_html_stripped():
    job = ArbeitnowFetcher()._parse_item(make_item())
    assert "<strong>" not in job.description
    assert "LLM" in job.description


def test_posted_date_parsed_from_unix_seconds():
    job = ArbeitnowFetcher()._parse_item(make_item())
    assert job.posted_date is not None and job.posted_date.year == 2026
    assert job.posted_date.tzinfo is not None


def test_job_types_become_contract_type_guess():
    assert ArbeitnowFetcher()._parse_item(make_item()).contract_type_guess == ContractTypeGuess.EMPLOYMENT
    freelance = ArbeitnowFetcher()._parse_item(make_item(job_types=["Freelance"]))
    assert freelance.contract_type_guess == ContractTypeGuess.FREELANCE


def test_seniority_labels_in_job_types_stay_unclear():
    # Arbeitnow mixes seniority into job_types; these say nothing about the
    # engagement type, so UNCLEAR is the correct answer, not a wrong guess.
    for labels in (["berufserfahren"], ["Mid"], ["berufseinstieg"], []):
        job = ArbeitnowFetcher()._parse_item(make_item(job_types=labels))
        assert job.contract_type_guess == ContractTypeGuess.UNCLEAR, labels


def test_remote_flag_rendered_into_location_text():
    assert ArbeitnowFetcher()._parse_item(make_item(remote=False)).location_text == "Berlin, vor Ort"
    assert ArbeitnowFetcher()._parse_item(make_item(remote=True)).location_text == "Berlin, Remote"
    assert ArbeitnowFetcher()._parse_item(make_item(location="", remote=True)).location_text == "Remote"


def test_onsite_german_job_is_excluded_by_stage_one():
    # The point of rendering `remote` into location_text: eligibility reads
    # text, so a bare "Berlin" would otherwise pass as eligible.
    config = FilterConfig(
        hybrid_onsite_phrases=["hybrid", "onsite", "vor ort"],
        remote_indicator_phrases=["remote", "remote möglich"],
    )
    onsite = apply_eligibility_filter(ArbeitnowFetcher()._parse_item(make_item(remote=False)), config)
    assert onsite.eligibility_bucket == EligibilityBucket.EXCLUDED

    remote = apply_eligibility_filter(ArbeitnowFetcher()._parse_item(make_item(remote=True)), config)
    assert remote.eligibility_bucket == EligibilityBucket.ELIGIBLE


def test_parse_item_returns_none_without_title_url_or_slug():
    assert ArbeitnowFetcher()._parse_item(make_item(title="")) is None
    assert ArbeitnowFetcher()._parse_item(make_item(url="")) is None
    assert ArbeitnowFetcher()._parse_item(make_item(slug="")) is None


def test_offsite_company_url_is_kept_as_is():
    # Reconstructing an arbeitnow.com URL for an externally-hosted listing
    # returns 410 — a dead link is worse than the real company one.
    job = ArbeitnowFetcher()._parse_item(make_item(url="https://www.preiswecker.com/"))
    assert job.url == "https://www.preiswecker.com/"


def test_fetch_paginates_and_stops_on_empty_page(monkeypatch):
    pages = []

    def fake_get(url, params=None, headers=None, timeout=None):
        pages.append(params["page"])
        if params["page"] <= 2:
            return FakeResponse({"data": [make_item(slug=f"job-{params['page']}-{i}") for i in range(3)]})
        return FakeResponse({"data": []})

    monkeypatch.setattr(arbeitnow_module.requests, "get", fake_get)
    jobs = ArbeitnowFetcher().fetch()
    assert pages == [1, 2, 3]
    assert len(jobs) == 6


def test_fetch_dedupes_repeated_slugs_across_pages(monkeypatch):
    # Measured live: 576 records across 5 pages contained only 559 distinct
    # slugs — the API genuinely repeats jobs between pages.
    monkeypatch.setattr(
        arbeitnow_module.requests, "get", lambda *a, **k: FakeResponse({"data": [make_item(slug="same-job")]})
    )
    assert len(ArbeitnowFetcher().fetch()) == 1


def test_fetch_respects_max_pages(monkeypatch):
    pages = []

    def fake_get(url, params=None, headers=None, timeout=None):
        pages.append(params["page"])
        return FakeResponse({"data": [make_item(slug=f"job-{params['page']}")]})

    monkeypatch.setattr(arbeitnow_module.requests, "get", fake_get)
    ArbeitnowFetcher().fetch()
    assert pages == list(range(1, arbeitnow_module._MAX_PAGES + 1))


def test_fetch_returns_empty_on_request_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(arbeitnow_module.requests, "get", boom)
    assert ArbeitnowFetcher().fetch() == []


def test_fetch_keeps_earlier_pages_when_a_later_page_fails(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if params["page"] == 1:
            return FakeResponse({"data": [make_item(slug="job-1")]})
        raise RuntimeError("network down")

    monkeypatch.setattr(arbeitnow_module.requests, "get", fake_get)
    assert len(ArbeitnowFetcher().fetch()) == 1


def test_fetch_returns_empty_on_unexpected_shape(monkeypatch):
    monkeypatch.setattr(arbeitnow_module.requests, "get", lambda *a, **k: FakeResponse([1, 2, 3]))
    assert ArbeitnowFetcher().fetch() == []


def test_fetch_survives_one_malformed_item(monkeypatch):
    monkeypatch.setattr(
        arbeitnow_module.requests, "get", lambda *a, **k: FakeResponse({"data": [None, make_item(slug="ok")]})
    )
    assert len(ArbeitnowFetcher().fetch()) == 1
