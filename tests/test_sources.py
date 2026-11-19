from __future__ import annotations

import json

import pytest

from jobpulse.adapters.http import FakeHttpClient
from jobpulse.sources import ats, simplify
from tests.conftest import fixture_text


def test_parse_listings_filters_and_dedupes() -> None:
    raw = json.loads(fixture_text("listings.json"))
    got = simplify.dedupe(simplify.parse_listings(raw, "2026-11-18", lookback_days=1))
    assert [x.id for x in got] == ["gh-1", "lv-1", "broken-1", "icims-1", "gh-2"]
    assert all(x.date_posted in ("2026-11-17", "2026-11-18") for x in got)


def test_lookback_zero_only_takes_run_date() -> None:
    raw = json.loads(fixture_text("listings.json"))
    got = simplify.parse_listings(raw, "2026-11-17", lookback_days=0)
    assert [x.id for x in got] == ["gh-1"]


@pytest.mark.parametrize(
    ("loc", "us"),
    [
        ("Seattle, WA", True),
        ("Remote in USA", True),
        ("Toronto, ON, Canada", False),
        ("London, UK", False),
        ("Washington, DC", True),
        ("United States", True),
    ],
)
def test_is_us_location(loc: str, us: bool) -> None:
    assert simplify.is_us_location(loc) is us


def test_greenhouse_fetch(fake_http: FakeHttpClient) -> None:
    job = ats.fetch_job_text(fake_http, "https://job-boards.greenhouse.io/acme/jobs/1001?utm_source=Simplify")
    assert job is not None and job.ats == "greenhouse" and job.open
    assert "Python, C++ and Linux" in job.text
    assert "<li>" not in job.text and "&lt;" not in job.text


def test_lever_fetch_joins_lists(fake_http: FakeHttpClient) -> None:
    job = ats.fetch_job_text(
        fake_http, "https://jobs.lever.co/beta/11111111-2222-3333-4444-555555555555/apply"
    )
    assert job is not None and job.ats == "lever"
    assert "Hugging Face" in job.text and "No visa sponsorship" in job.text


def test_closed_greenhouse_posting_is_reported_closed() -> None:
    job = ats.fetch_job_text(FakeHttpClient({}), "https://job-boards.greenhouse.io/acme/jobs/999")
    assert job is not None and job.open is False


def test_unsupported_url_returns_none() -> None:
    assert ats.fetch_job_text(FakeHttpClient({}), "https://careers.example.com/jobs/1") is None
    assert not ats.supported("https://careers-epsilon.icims.com/jobs/1/job")


def test_workday_url_parsing() -> None:
    url = "https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/Seattle-WA/SWE-Intern_R123?utm_source=x"
    body = json.dumps({"jobPostingInfo": {"title": "SWE Intern", "jobDescription": "<p>Python</p>"}})
    http = FakeHttpClient(
        {"https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Careers/job/Seattle-WA/SWE-Intern_R123": body}
    )
    job = ats.fetch_job_text(http, url)
    assert job is not None and job.text == "Python"


def test_html_to_text() -> None:
    assert ats.html_to_text("<ul><li>A</li><li>B&amp;C</li></ul>") == "- A\n\n- B&C"
