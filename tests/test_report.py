from jobscout.models import ContractTypeGuess, EligibilityBucket, Job
from jobscout.report import print_summary


def make_job(score: int, title: str = "Engineer") -> Job:
    return Job(
        title=title,
        company="Acme",
        description="x",
        url="https://example.com/job",
        source="test",
        score=score,
        eligibility_bucket=EligibilityBucket.ELIGIBLE,
        contract_type_guess=ContractTypeGuess.UNCLEAR,
    )


def test_min_score_threshold_excludes_low_scoring_jobs_from_top_n(capsys, tmp_path):
    jobs = [make_job(80, "High"), make_job(20, "Low"), make_job(50, "Mid")]
    report_path = str(tmp_path / "report.html")
    (tmp_path / "report.html").write_text("<html></html>")

    print_summary(3, 3, jobs, report_path, min_score_threshold=30)

    out = capsys.readouterr().out
    assert "High" in out
    assert "Mid" in out
    assert "Low" not in out


def test_min_score_threshold_zero_shows_everything(capsys, tmp_path):
    jobs = [make_job(80, "High"), make_job(0, "Zero")]
    report_path = str(tmp_path / "report.html")
    (tmp_path / "report.html").write_text("<html></html>")

    print_summary(2, 2, jobs, report_path, min_score_threshold=0)

    out = capsys.readouterr().out
    assert "High" in out
    assert "Zero" in out


def test_no_jobs_above_threshold_prints_message_not_empty_table(capsys, tmp_path):
    jobs = [make_job(10, "Low")]
    report_path = str(tmp_path / "report.html")
    (tmp_path / "report.html").write_text("<html></html>")

    print_summary(1, 1, jobs, report_path, min_score_threshold=30)

    out = capsys.readouterr().out
    assert "No eligible/needs_review jobs scoring >= 30" in out