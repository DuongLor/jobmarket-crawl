"""HTTP lịch sự: tôn trọng robots.txt, giãn cách request, dừng khi bị chặn.

Gặp trang chống bot (Cloudflare challenge…) → ném `Blocked` và dừng nguồn đó.
KHÔNG thử vượt: không đổi User-Agent, không giải challenge, không dùng proxy.
"""

import time
import urllib.robotparser
from urllib.parse import urlsplit

import httpx

CHALLENGE_MARKERS = ("just a moment", "cf-chl", "enable javascript and cookies", "captcha")


class Blocked(Exception):
    """Nguồn chặn truy cập tự động (challenge / 403 / 429 kéo dài)."""


class Disallowed(Exception):
    """robots.txt không cho phép URL này."""


class PoliteClient:
    def __init__(self, cfg: dict, transport: httpx.BaseTransport | None = None):
        self.user_agent = cfg["user_agent"]
        self.min_delay = float(cfg["min_delay_seconds"])
        self.max_retries = int(cfg["max_retries"])
        self._client = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept-Language": "vi,en;q=0.8"},
            timeout=float(cfg["timeout_seconds"]),
            follow_redirects=True,
            transport=transport,
        )
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_request_at: dict[str, float] = {}
        self.requests_made = 0

    def close(self) -> None:
        self._client.close()

    def _wait_turn(self, host: str) -> None:
        last = self._last_request_at.get(host)
        if last is not None:
            delay = self.min_delay - (time.monotonic() - last)
            if delay > 0:
                time.sleep(delay)
        self._last_request_at[host] = time.monotonic()

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            self._wait_turn(parts.netloc)
            resp = self._client.get(f"{origin}/robots.txt")
            self.requests_made += 1
            # RFC 9309: 4xx (không có robots.txt) = không hạn chế; 5xx (máy chủ lỗi) = coi như cấm hết
            if resp.status_code >= 500:
                parser.parse(["User-agent: *", "Disallow: /"])
            else:
                parser.parse(resp.text.splitlines() if resp.status_code == 200 else [])
            self._robots[origin] = parser
        return self._robots[origin]

    def get(self, url: str) -> str:
        if not self._robots_for(url).can_fetch(self.user_agent, url):
            raise Disallowed(url)
        host = urlsplit(url).netloc
        for attempt in range(self.max_retries + 1):
            self._wait_turn(host)
            resp = self._client.get(url)
            self.requests_made += 1
            body = resp.text
            if is_challenge(resp.status_code, body):
                raise Blocked(f"{resp.status_code} challenge at {url}")
            if resp.status_code in (429, 503) and attempt < self.max_retries:
                time.sleep(self.min_delay * (2 ** (attempt + 1)))
                continue
            if resp.status_code in (403, 429):
                raise Blocked(f"{resp.status_code} at {url}")
            resp.raise_for_status()
            return body
        raise Blocked(f"retries exhausted at {url}")


# Trang xác minh người dùng trả HTTP 200 (CareerLink, 2026-11-03: trang chi tiết hiện hộp "Xác nhận bạn không phải
# robot" thay vì nội dung tin). Câu này không có trên trang thường → coi là bị chặn.
CHALLENGE_TEXT_200 = ("bạn không phải robot", "ban khong phai robot", "you are not a robot", "verify you are human")


def is_challenge(status: int, body: str) -> bool:
    lowered = body.lower()
    if status == 200:
        return any(marker in lowered for marker in CHALLENGE_TEXT_200)
    if status not in (403, 429, 503):
        return False
    return any(marker in lowered[:4000] for marker in CHALLENGE_MARKERS)
