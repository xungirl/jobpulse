"""Polite HTTP client: generic User-Agent, robots.txt, per-host rate limit, retries."""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections.abc import Callable

from jobpulse.ports import HttpError

# Generic, no personal information (no name/email). Identifies the tool only.
USER_AGENT = "jobpulse/0.1 (open-source job-market research bot)"


class UrllibHttpClient:
    def __init__(
        self,
        min_interval_s: float = 0.5,
        timeout_s: float = 20.0,
        retries: int = 2,
        respect_robots: bool = True,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        self.retries = retries
        self.respect_robots = respect_robots
        self._sleep = sleep
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.requests_made = 0

    def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urllib.parse.urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                body = self._raw_get(origin + "/robots.txt", {"Accept": "text/plain"})
                rp.parse(body.splitlines())
                self._robots[origin] = rp
            except HttpError:
                self._robots[origin] = None  # no robots.txt (404) -> allowed
        rp_or_none = self._robots[origin]
        return rp_or_none is None or rp_or_none.can_fetch(USER_AGENT, url)

    def _throttle(self, host: str) -> None:
        wait = self._last_hit.get(host, 0.0) + self.min_interval_s - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        self._last_hit[host] = time.monotonic()

    def _raw_get(self, url: str, headers: dict[str, str]) -> str:
        host = urllib.parse.urlparse(url).netloc
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
        for attempt in range(self.retries + 1):
            self._throttle(host)
            self.requests_made += 1
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    return str(r.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as e:
                retryable = e.code == 429 or e.code >= 500
                if not retryable or attempt == self.retries:
                    raise HttpError(url, e.code, str(e.reason)) from e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt == self.retries:
                    raise HttpError(url, None, str(e)) from e
            self._sleep(2.0 * (attempt + 1))  # linear backoff before retrying
        raise HttpError(url, None, "unreachable")

    def get_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        if not self._allowed(url):
            raise HttpError(url, None, "disallowed by robots.txt")
        return self._raw_get(url, {"Accept": "application/json", **(headers or {})})


class FakeHttpClient:
    """Serves canned responses for tests; unknown URLs raise HttpError(404)."""

    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        self.calls.append(url)
        if url in self.responses:
            return self.responses[url]
        for prefix, body in self.responses.items():
            if prefix.endswith("*") and url.startswith(prefix[:-1]):
                return body
        raise HttpError(url, 404, "not found (fake)")
