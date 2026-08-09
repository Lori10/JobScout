from jobscout.dedupe import dedupe, normalize_url
from jobscout.models import Job


def make_job(title, company, url, source, description="") -> Job:
    return Job(title=title, company=company, description=description, url=url, source=source)


def test_normalize_url_ignores_www_trailing_slash_and_scheme():
    assert normalize_url("https://www.example.com/jobs/123/") == normalize_url("http://example.com/jobs/123")


def test_same_url_different_source_deduped():
    jobs = [
        make_job("Senior LLM Engineer", "Acme", "https://example.com/jobs/123", "remoteok"),
        make_job("Senior LLM Engineer", "Acme", "https://www.example.com/jobs/123/", "remotive"),
    ]
    result = dedupe(jobs)
    assert len(result) == 1


def test_fuzzy_company_title_match_deduped():
    jobs = [
        make_job("Senior LLM Engineer", "Acme Inc", "https://boards.example.com/acme/llm-eng", "remoteok"),
        make_job("Sr. LLM Engineer", "Acme", "https://acme.com/careers/llm-eng-role", "remotive"),
    ]
    result = dedupe(jobs)
    assert len(result) == 1


def test_distinct_jobs_not_deduped():
    jobs = [
        make_job("Senior LLM Engineer", "Acme", "https://acme.com/jobs/1", "remoteok"),
        make_job("Warehouse Associate", "Beta Logistics", "https://beta.com/jobs/2", "remotive"),
    ]
    result = dedupe(jobs)
    assert len(result) == 2


def test_prefer_richer_description_and_merge_tags():
    job_a = make_job("LLM Engineer", "Acme", "https://acme.com/jobs/1", "remoteok", description="Short.")
    job_a.tags = ["python"]
    job_b = make_job(
        "LLM Engineer",
        "Acme",
        "https://acme.com/jobs/1",
        "remotive",
        description="A much longer and more detailed job description with lots of context.",
    )
    job_b.tags = ["llm", "rag"]
    result = dedupe([job_a, job_b])
    assert len(result) == 1
    assert "longer" in result[0].description
    assert set(result[0].tags) == {"python", "llm", "rag"}
