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


# --- Phase 4 groundwork: source-assigned unique role ids ---


def test_unique_role_id_source_keeps_similar_titles_from_same_company():
    # A job-board API that assigns its own id/URL per role: two genuinely
    # different roles at one company must survive, however similar the
    # titles read. Same failure the Ashby/Greenhouse skip was added for.
    jobs = [
        make_job(
            "Senior Python Engineer", "Acme", "https://himalayas.app/jobs/acme-senior-python-engineer", "himalayas"
        ),
        make_job(
            "Senior Python Engineer II",
            "Acme",
            "https://himalayas.app/jobs/acme-senior-python-engineer-ii",
            "himalayas",
        ),
    ]
    assert len(dedupe(jobs)) == 2


def test_unique_role_id_source_still_dedupes_across_sources():
    # The skip is same-source only: cross-source mirroring is exactly what
    # fuzzy matching exists for and must keep working.
    jobs = [
        make_job("Senior LLM Engineer", "Acme Inc", "https://himalayas.app/jobs/acme-senior-llm-engineer", "himalayas"),
        make_job("Sr. LLM Engineer", "Acme", "https://jobicy.com/jobs/999-sr-llm-engineer", "jobicy"),
    ]
    assert len(dedupe(jobs)) == 1


def test_unique_role_id_source_still_dedupes_identical_urls():
    # Exact normalized-URL matching runs before fuzzy and is unaffected.
    jobs = [
        make_job("Senior LLM Engineer", "Acme", "https://himalayas.app/jobs/acme-llm", "himalayas"),
        make_job("Senior LLM Engineer", "Acme", "https://www.himalayas.app/jobs/acme-llm/", "himalayas"),
    ]
    assert len(dedupe(jobs)) == 1


def test_weworkremotely_placeholder_companies_are_not_merged():
    # WWR parses company out of a "Company: Role" RSS title; when the
    # separator is missing both jobs share a placeholder company, which
    # scores company_similarity 1.0 and would sail past the guard.
    jobs = [
        make_job(
            "Backend Engineer", "(company not stated)", "https://weworkremotely.com/remote-jobs/a", "weworkremotely"
        ),
        make_job(
            "Backend Engineer II", "(company not stated)", "https://weworkremotely.com/remote-jobs/b", "weworkremotely"
        ),
    ]
    assert len(dedupe(jobs)) == 2


def test_lever_expansion_roles_not_merged():
    # Same guarantee Ashby/Greenhouse already had, extended to Lever.
    jobs = [
        make_job(
            "Account Executive", "Acme", "https://jobs.lever.co/acme/aaa", "hn_whoishiring", source_id="42:lever:aaa"
        ),
        make_job(
            "Account Executive - NYC",
            "Acme",
            "https://jobs.lever.co/acme/bbb",
            "hn_whoishiring",
            source_id="42:lever:bbb",
        ),
    ]
    assert len(dedupe(jobs)) == 2


def test_ordinary_sources_still_fuzzy_merge_unchanged():
    # Phase 1 behavior must be untouched: remoteok/remotive are deliberately
    # not in _UNIQUE_ROLE_ID_SOURCES.
    jobs = [
        make_job("Senior LLM Engineer", "Acme Inc", "https://remoteok.com/l/1", "remoteok"),
        make_job("Sr. LLM Engineer", "Acme", "https://remoteok.com/l/2", "remoteok"),
    ]
    assert len(dedupe(jobs)) == 1


def test_bulleted_role_link_jobs_not_merged():
    # Same guarantee as the ATS-expansion markers, extended to
    # hn_whoishiring._bulleted_role_link_jobs's ":bullet:" marker — several
    # roles from one company, each with its own real URL, must not be
    # fuzzy-merged into each other just for having similar titles.
    jobs = [
        make_job(
            "Senior Full-Stack Engineer",
            "Mitte",
            "https://mitte.ai/careers/role/aaa",
            "hn_whoishiring",
            source_id="42:bullet:0",
        ),
        make_job(
            "Senior Backend Engineer",
            "Mitte",
            "https://mitte.ai/careers/role/bbb",
            "hn_whoishiring",
            source_id="42:bullet:1",
        ),
    ]
    assert len(dedupe(jobs)) == 2
