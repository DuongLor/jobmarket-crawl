"""robots.txt RFC 9309 (ký tự đại diện, khớp dài nhất) + chuyển hướng phải qua robots + 429 dừng ngay."""

import httpx
import pytest

from jobmarket.fetch.client import Blocked, Disallowed, PoliteClient
from jobmarket.fetch.robots import RobotsRules

UA = "jobmarket-crawl/0.1 (personal, non-commercial, low-rate)"
ROBOTS = """
User-agent: *
Disallow: /*?page=
Disallow: /vi/jobs/*
Disallow: /*?q=
Disallow: /*.pdf$
Allow: /vi/jobs/public$
Disallow: /admin

User-agent: OtherBot
Disallow: /
"""


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/a?page=2", False),  # urllib.robotparser trả True (lỗi H2 của review)
        ("/vi/jobs/abc", False),
        ("/tim?q=1", False),
        ("/tim?sort=1&q=1", True),  # "?q=" phải đứng ngay sau "?"
        ("/file.pdf", False),
        ("/file.pdf?x=1", True),  # "$" = hết URL
        ("/vi/jobs/public", True),  # Allow dài hơn thắng
        ("/administrator", False),  # tiền tố
        ("/viec-lam/tat-ca-viec-lam-vi.html", True),
        ("/robots.txt", True),
    ],
)
def test_wildcards_and_longest_match(path, allowed):
    assert RobotsRules(ROBOTS.splitlines()).can_fetch(UA, f"https://x.vn{path}") is allowed


def test_named_group_wins_over_star():
    rules = RobotsRules(["User-agent: *", "Disallow: /", "", "User-agent: jobmarket-crawl", "Allow: /"])
    assert rules.can_fetch(UA, "https://x.vn/a")
    assert not rules.can_fetch("OtherBot/1.0", "https://x.vn/a")


def test_empty_disallow_allows_all_and_tie_prefers_allow():
    assert RobotsRules(["User-agent: *", "Disallow:"]).can_fetch(UA, "https://x.vn/a")
    assert RobotsRules(["User-agent: *", "Disallow: /a", "Allow: /a"]).can_fetch(UA, "https://x.vn/a")


CFG = {"user_agent": UA, "min_delay_seconds": 0, "max_retries": 2, "timeout_seconds": 5}


def _client(handler) -> PoliteClient:
    return PoliteClient(CFG, transport=httpx.MockTransport(handler))


def test_redirect_target_is_checked_against_robots():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        if request.url.path == "/ok":
            return httpx.Response(302, headers={"location": "/private/data"})
        return httpx.Response(200, text="bí mật")

    with pytest.raises(Disallowed):
        _client(handler).get("https://x.vn/ok")


def test_allowed_redirect_is_followed():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://x.vn/new"})
        return httpx.Response(200, text="nội dung")

    client = _client(handler)
    assert client.get("https://x.vn/old") == "nội dung" and client.requests_made == 3


def test_429_stops_immediately_without_retry():
    calls = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "3600"})

    with pytest.raises(Blocked):
        _client(handler).get("https://x.vn/a")
    assert len(calls) == 1


def test_503_with_long_retry_after_stops():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(503, headers={"retry-after": "3600"}, text="bảo trì")

    with pytest.raises(Blocked):
        _client(handler).get("https://x.vn/a")
