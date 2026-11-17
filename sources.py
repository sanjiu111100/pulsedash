"""Collectors for public data sources.

Design notes
------------
* Every collector is isolated. A failing source records its error and returns
  an empty list; it must never abort the whole run.
* All network access goes through `HttpClient`, which adds a User-Agent,
  a hard timeout, bounded retries with exponential backoff, and a per-client
  minimum interval between requests (politeness / rate limiting).
* Timestamps are stored as UTC epoch seconds.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

USER_AGENT = "PulseDash/1.0 (+https://github.com/) Python-urllib"


@dataclass
class Point:
    """One numeric observation."""

    source: str
    metric: str
    ts: int
    value: float
    meta: str = ""


@dataclass
class SourceResult:
    """Outcome of one source run."""

    name: str
    ok: bool
    points: list[Point] = field(default_factory=list)
    elapsed_ms: int = 0
    error: str = ""


class HttpError(RuntimeError):
    pass


class HttpClient:
    """Small HTTP client with timeout, retry and throttling."""

    def __init__(self, timeout: int = 15, retries: int = 3,
                 backoff_ms: int = 800, min_interval_ms: int = 300) -> None:
        self.timeout = timeout
        self.retries = max(1, retries)
        self.backoff_ms = backoff_ms
        self.min_interval_s = min_interval_ms / 1000.0
        self._last_request = 0.0

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_request
        if gap < self.min_interval_s:
            time.sleep(self.min_interval_s - gap)
        self._last_request = time.monotonic()

    def get_text(self, url: str, headers: dict | None = None) -> str:
        last_error = ""
        for attempt in range(1, self.retries + 1):
            self._throttle()
            request_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
            if headers:
                request_headers.update(headers)
            request = urllib.request.Request(url, headers=request_headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    return response.read().decode(charset, errors="replace")
            except urllib.error.HTTPError as exc:
                last_error = "HTTP %s for %s" % (exc.code, url)
                if exc.code in (400, 401, 403, 404):
                    raise HttpError(last_error) from exc
            except Exception as exc:  # noqa: BLE001 - network layer, report and retry
                last_error = "%s: %s" % (type(exc).__name__, exc)
            if attempt < self.retries:
                time.sleep(self.backoff_ms / 1000.0 * (2 ** (attempt - 1)))
        raise HttpError(last_error or "unknown network error")

    def get_json(self, url: str, headers: dict | None = None):
        payload = self.get_text(url, headers=headers)
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise HttpError("invalid JSON from %s: %s" % (url, exc)) from exc


class Collector:
    """Base class: give a name and implement `fetch`."""

    name = "base"

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def fetch(self) -> list[Point]:
        raise NotImplementedError


class WeatherCollector(Collector):
    """Hourly temperature / humidity / wind for one city via wttr.in.

    wttr.in reports local wall-clock time for the requested city, so the
    UTC offset is applied explicitly before converting to epoch seconds.
    """

    name = "weather"

    def __init__(self, http: HttpClient, city: str, utc_offset_hours: int = 8) -> None:
        super().__init__(http)
        self.city = city
        self.tz = timezone(timedelta(hours=utc_offset_hours))

    def fetch(self) -> list[Point]:
        url = "https://wttr.in/%s?format=j1" % urllib.parse.quote(self.city)
        data = self.http.get_json(url)
        points: list[Point] = []
        days = data.get("weather") or []
        for day in days[:3]:
            date_text = day.get("date")
            if not date_text:
                continue
            for hour in day.get("hourly") or []:
                clock = str(hour.get("time", "0")).zfill(4)
                try:
                    stamp = datetime.strptime(date_text + clock, "%Y-%m-%d%H%M")
                except ValueError:
                    continue
                epoch = int(stamp.replace(tzinfo=self.tz).timestamp())
                for metric, key in (("temp_c", "tempC"), ("humidity_pct", "humidity"),
                                    ("wind_kmph", "windspeedKmph")):
                    raw = hour.get(key)
                    if raw in (None, ""):
                        continue
                    try:
                        points.append(Point(self.name, metric, epoch, float(raw), self.city))
                    except (TypeError, ValueError):
                        continue
        current = (data.get("current_condition") or [{}])[0]
        now = int(datetime.now(tz=self.tz).timestamp())
        for metric, key in (("temp_c", "temp_C"), ("humidity_pct", "humidity")):
            raw = current.get(key)
            if raw not in (None, ""):
                try:
                    points.append(Point(self.name, metric, now, float(raw), self.city + " (current)"))
                except (TypeError, ValueError):
                    pass
        return points


class FxCollector(Collector):
    """Daily reference exchange rate over a rolling window via frankfurter.app."""

    name = "fx"

    def __init__(self, http: HttpClient, base: str, quote: str, days: int = 30) -> None:
        super().__init__(http)
        self.base = base.upper()
        self.quote = quote.upper()
        self.days = days

    def fetch(self) -> list[Point]:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=self.days)
        url = "https://api.frankfurter.app/%s..%s?from=%s&to=%s" % (
            start.isoformat(), end.isoformat(), self.base, self.quote)
        data = self.http.get_json(url)
        metric = "rate_%s_%s" % (self.base, self.quote)
        points: list[Point] = []
        for date_text, payload in sorted((data.get("rates") or {}).items()):
            value = payload.get(self.quote)
            if value is None:
                continue
            try:
                stamp = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            points.append(Point(self.name, metric, int(stamp.timestamp()), float(value), date_text))
        return points


class GithubCollector(Collector):
    """Repository popularity counters from the public GitHub REST API."""

    name = "github"

    def __init__(self, http: HttpClient, repos: list[str]) -> None:
        super().__init__(http)
        self.repos = repos

    def fetch(self) -> list[Point]:
        now = int(datetime.now(timezone.utc).timestamp())
        points: list[Point] = []
        for full_name in self.repos:
            url = "https://api.github.com/repos/%s" % full_name
            data = self.http.get_json(url, headers={"Accept": "application/vnd.github+json"})
            for metric, key in (("stars", "stargazers_count"), ("forks", "forks_count"),
                                ("open_issues", "open_issues_count")):
                value = data.get(key)
                if value is None:
                    continue
                points.append(Point(self.name, "%s::%s" % (full_name, metric), now, float(value), full_name))
        return points


def build_collectors(config: dict, http: HttpClient) -> list[Collector]:
    """Instantiate enabled collectors from config."""
    spec = config.get("sources") or {}
    collectors: list[Collector] = []

    weather = spec.get("weather") or {}
    if weather.get("enabled"):
        collectors.append(WeatherCollector(
            http, weather.get("city", "Shanghai"), int(weather.get("utc_offset_hours", 8))))

    fx = spec.get("fx") or {}
    if fx.get("enabled"):
        collectors.append(FxCollector(
            http, fx.get("base", "USD"), fx.get("quote", "CNY"), int(fx.get("days", 30))))

    github = spec.get("github") or {}
    if github.get("enabled"):
        collectors.append(GithubCollector(http, list(github.get("repos") or [])))

    return collectors
