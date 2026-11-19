"""Fetch full JD text from public applicant-tracking-system (ATS) JSON APIs.

Adapted from `skills/jd-resume-tailor/fetch_jd.py` in jd-resume-tailor
(https://github.com/xungirl/jd-resume-tailor, MIT License, (c) 2026 xungirl).
Changes: typed, takes an injected HttpClient (polite UA, robots.txt, rate
limit), returns a dataclass, and drops the generic-HTML fallback and the
redirect resolver (we only fetch structured public APIs).

Most career sites are JavaScript apps, so scraping the HTML yields nothing; but
the big ATSs expose each posting through a public JSON endpoint.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from jobpulse.ports import HttpClient, HttpError


@dataclass(frozen=True)
class JobText:
    ats: str
    title: str | None
    location: str | None
    open: bool
    text: str


def html_to_text(s: str | None) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<(br|/p|/li|/h\d|/div)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<li[^>]*>", "\n- ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


def _json(http: HttpClient, url: str) -> Any:
    return json.loads(http.get_text(url))


def greenhouse(http: HttpClient, url: str) -> JobText | None:
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", url)
    if not m:
        return None  # company-domain gh_jid links don't name the board; skip rather than guess
    board = m.group(1)
    jid = (q.get("gh_jid") or q.get("token") or [m.group(2)])[0]
    try:
        d = _json(http, f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid}")
    except HttpError as e:
        if e.status == 404:
            return JobText("greenhouse", None, None, False, "")
        raise
    return JobText(
        "greenhouse", d["title"], (d.get("location") or {}).get("name"), True, html_to_text(d.get("content"))
    )


def ashby(http: HttpClient, url: str) -> JobText | None:
    m = re.search(r"ashbyhq\.com/([\w.%-]+)/([0-9a-f-]{36})", url)
    if not m:
        return None
    org, jid = m.groups()
    d = _json(http, f"https://api.ashbyhq.com/posting-api/job-board/{org}")
    for j in d.get("jobs", []):
        if j["id"] == jid:
            return JobText(
                "ashby",
                j["title"],
                j.get("location"),
                True,
                j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
            )
    return JobText("ashby", None, None, False, "")


def lever(http: HttpClient, url: str) -> JobText | None:
    m = re.search(r"lever\.co/([\w.-]+)/([0-9a-f-]{36})", url)
    if not m:
        return None
    org, jid = m.groups()
    try:
        d = _json(http, f"https://api.lever.co/v0/postings/{org}/{jid}")
    except HttpError as e:
        if e.status == 404:
            return JobText("lever", None, None, False, "")
        raise
    lists = "\n\n".join(f"{x['text']}\n{html_to_text(x['content'])}" for x in d.get("lists", []))
    body = d.get("descriptionPlain", "") + "\n\n" + lists + "\n\n" + d.get("additionalPlain", "")
    return JobText("lever", d["text"], (d.get("categories") or {}).get("location"), True, body.strip())


def workday(http: HttpClient, url: str) -> JobText | None:
    m = re.search(
        r"https://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/job/(.+?)(?:\?|$)",
        url,
    )
    if not m:
        return None
    tenant, wd, site, path = m.groups()
    path = path.removesuffix("/apply")
    try:
        d = _json(http, f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/job/{path}")
    except HttpError as e:
        if e.status in (404, 410):
            return JobText("workday", None, None, False, "")
        raise
    j = d["jobPostingInfo"]
    return JobText(
        "workday",
        j["title"],
        j.get("location"),
        bool(j.get("canApply", True)),
        html_to_text(j.get("jobDescription")),
    )


def oracle(http: HttpClient, url: str) -> JobText | None:
    m = re.search(
        r"https://([\w.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[a-z]{2}/sites/([\w-]+)/job/(\d+)",
        url,
    )
    if not m:
        return None
    host, site, jid = m.groups()
    api = (
        f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
        f"?expand=all&onlyData=true&finder=ById;Id=%22{jid}%22,siteNumber={site}"
    )
    items = _json(http, api).get("items") or []
    if not items:
        return JobText("oracle", None, None, False, "")
    j = items[0]
    keys = ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")
    body = "\n\n".join(html_to_text(j.get(k)) for k in keys if j.get(k))
    return JobText("oracle", j["Title"], j.get("PrimaryLocation"), True, body)


def smartrecruiters(http: HttpClient, url: str) -> JobText | None:
    m = re.search(r"smartrecruiters\.com/([\w-]+)/(\d+)", url)
    if not m:
        return None
    co, jid = m.groups()
    try:
        d = _json(http, f"https://api.smartrecruiters.com/v1/companies/{co}/postings/{jid}")
    except HttpError as e:
        if e.status == 404:
            return JobText("smartrecruiters", None, None, False, "")
        raise
    sec = (d.get("jobAd") or {}).get("sections") or {}
    body = "\n\n".join(
        f"{v.get('title', '')}\n{html_to_text(v.get('text'))}" for v in sec.values() if isinstance(v, dict)
    )
    loc = d.get("location") or {}
    return JobText("smartrecruiters", d.get("name"), loc.get("city"), bool(d.get("active", True)), body)


FETCHERS: list[Callable[[HttpClient, str], JobText | None]] = [
    ashby,
    lever,
    workday,
    oracle,
    smartrecruiters,
    greenhouse,
]


def supported(url: str) -> bool:
    return any(
        h in url
        for h in (
            "ashbyhq.com",
            "lever.co",
            "myworkdayjobs.com",
            "oraclecloud.com",
            "smartrecruiters.com",
            "greenhouse.io",
        )
    )


def fetch_job_text(http: HttpClient, url: str) -> JobText | None:
    """JD text for a supported ATS URL; None if the URL is not a supported ATS.

    Network errors propagate as HttpError so the caller can count and skip them.
    """
    for fetcher in FETCHERS:
        result = fetcher(http, url)
        if result is not None:
            return result
    return None
