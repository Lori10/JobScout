import pytest

from jobscout.fetchers.remoteok import NotAJobError, RemoteOKFetcher


def make_item(**overrides) -> dict:
    defaults = dict(
        id="123",
        position="Senior ML Engineer",
        company="Acme",
        description="Build ML pipelines.",
        url="https://remoteok.com/remote-jobs/remote-senior-ml-engineer-acme-123",
        apply_url="",
        slug="remote-senior-ml-engineer-acme-123",
        date="2026-08-06T16:41:48+00:00",
        salary_min=0,
        salary_max=0,
        tags=["engineer"],
        location="",
    )
    defaults.update(overrides)
    return defaults


def test_real_job_is_parsed_normally():
    job = RemoteOKFetcher()._parse_item(make_item())
    assert job.title == "Senior ML Engineer"
    assert job.company == "Acme"


def test_empty_slug_and_generic_listing_url_is_rejected():
    # Real case: a promotional/non-job entry RemoteOK's own feed returned
    # with an empty slug and both url/apply_url falling back to the
    # generic listings page instead of a specific job permalink.
    item = make_item(
        position="IDEAS THAT STICK. literally ð",
        company="Sticky Today",
        slug="",
        url="https://remoteOK.com/remote-jobs/",
        apply_url="https://remoteOK.com/remote-jobs/",
    )
    with pytest.raises(NotAJobError):
        RemoteOKFetcher()._parse_item(item)


def test_empty_slug_alone_is_not_enough_to_reject():
    # A sparse-but-real listing with an empty slug but a real per-job URL
    # must NOT be rejected - only the combination is a strong signal.
    item = make_item(slug="", url="https://remoteok.com/remote-jobs/remote-ml-engineer-acme-999")
    job = RemoteOKFetcher()._parse_item(item)
    assert job.title == "Senior ML Engineer"


def test_generic_listing_url_alone_is_not_enough_to_reject():
    # A listing with a real slug should not be rejected even if url/apply_url
    # happen to be empty/generic for some other reason.
    item = make_item(slug="remote-senior-ml-engineer-acme-123", url="https://remoteok.com/remote-jobs/", apply_url="")
    job = RemoteOKFetcher()._parse_item(item)
    assert job.title == "Senior ML Engineer"


def test_fetch_skips_non_job_entries_without_raising(monkeypatch):
    good_item = make_item()
    bad_item = make_item(
        id="999",
        position="Promo",
        company="Spam Co",
        slug="",
        url="https://remoteok.com/remote-jobs",
        apply_url="https://remoteok.com/remote-jobs",
    )

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"legal": "notice"}, good_item, bad_item]

    monkeypatch.setattr("jobscout.fetchers.remoteok.requests.get", lambda *a, **k: FakeResponse())

    jobs = RemoteOKFetcher().fetch()
    assert len(jobs) == 1
    assert jobs[0].company == "Acme"