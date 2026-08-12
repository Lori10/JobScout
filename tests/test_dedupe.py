from jobscout.dedupe import dedupe, normalize_url
from jobscout.models import Job


def make_job(title, company, url, source, description="", source_id=None) -> Job:
    return Job(title=title, company=company, description=description, url=url, source=source, source_id=source_id)


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


def test_different_companies_sharing_a_generic_placeholder_title_not_deduped():
    # Real bug: two unrelated HN postings ("Foxglove" and "Retool") both
    # fell back to the identical generic "role not stated" placeholder
    # title when no segment in their header line looked role-like. The
    # long shared title text alone pushed combined-string similarity above
    # threshold even though the companies are completely unrelated,
    # silently discarding one of them.
    placeholder = "Role not stated in header — see description"
    jobs = [
        make_job(placeholder, "Foxglove", "https://foxglove.dev/", "hn_whoishiring", description="Foxglove desc"),
        make_job(placeholder, "Retool", "https://retool.com/careers", "hn_whoishiring", description="Retool desc"),
    ]
    result = dedupe(jobs)
    assert len(result) == 2
    companies = {j.company for j in result}
    assert companies == {"Foxglove", "Retool"}


def test_distinct_ats_expansion_roles_at_same_company_not_fuzzy_merged():
    # Real bug: two distinct Starbridge roles from the Ashby board
    # expansion, "Account Executive - Mid Market" and "Account Executive -
    # Mid Market | NYC" (different Ashby posting ids/URLs), scored 0.95
    # fuzzy similarity - above FUZZY_THRESHOLD - and got wrongly merged
    # into one Job despite being genuinely different postings.
    jobs = [
        make_job(
            "Account Executive - Mid Market",
            "Starbridge",
            "https://jobs.ashbyhq.com/starbridge/e0ae2e0a",
            "hn_whoishiring",
            source_id="123:ashby:e0ae2e0a",
        ),
        make_job(
            "Account Executive - Mid Market | NYC",
            "Starbridge",
            "https://jobs.ashbyhq.com/starbridge/e6e89d93",
            "hn_whoishiring",
            source_id="123:ashby:e6e89d93",
        ),
    ]
    result = dedupe(jobs)
    assert len(result) == 2


def test_ats_expansion_role_still_fuzzy_matched_against_a_non_expansion_duplicate():
    # The skip only applies when BOTH jobs are from an ATS expansion -
    # cross-source dedup (the fuzzy matcher's actual purpose) must still
    # work when one side is a normal fetcher result.
    jobs = [
        make_job(
            "Senior LLM Engineer",
            "Acme Inc",
            "https://jobs.ashbyhq.com/acme/abc123",
            "hn_whoishiring",
            source_id="1:ashby:abc123",
        ),
        make_job("Sr. LLM Engineer", "Acme", "https://acme.com/careers/llm-eng-role", "remotive"),
    ]
    result = dedupe(jobs)
    assert len(result) == 1


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
