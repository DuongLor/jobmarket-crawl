"""HTTP lịch sự: tôn trọng robots.txt, giãn cách request, dừng khi bị chặn.

Gặp trang chống bot (Cloudflare challenge…) → ném `Blocked` và dừng nguồn đó.
KHÔNG thử vượt: không đổi User-Agent, không giải challenge, không dùng proxy.
"""

import math
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx

from jobmarket.fetch.robots import RobotsRules

MAX_REDIRECTS = 5
# robots.txt đòi nghỉ lâu hơn mức này giữa 2 request → coi như site không muốn bot đọc (một lần chạy sẽ kéo hàng giờ)
MAX_CRAWL_DELAY = 120

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
            # Tự theo chuyển hướng để kiểm robots.txt + giãn cách cho TỪNG bước (kể cả sang host khác)
            follow_redirects=False,
            transport=transport,
        )
        self._robots: dict[str, RobotsRules] = {}
        self._host_delay: dict[str, float] = {}  # max(min_delay, Crawl-delay của robots) theo host
        self._last_request_at: dict[str, float] = {}
        self.requests_made = 0

    def close(self) -> None:
        self._client.close()

    def _wait_turn(self, host: str) -> None:
        last = self._last_request_at.get(host)
        if last is not None:
            delay = self._host_delay.get(host, self.min_delay) - (time.monotonic() - last)
            if delay > 0:
                time.sleep(delay)
        self._last_request_at[host] = time.monotonic()

    def _robots_for(self, url: str) -> RobotsRules:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._wait_turn(parts.netloc)
            # RFC 9309: theo tối đa 5 lần chuyển hướng khi đọc robots.txt
            resp = self._client.get(f"{origin}/robots.txt", follow_redirects=True)
            self.requests_made += 1
            # 4xx (không có robots.txt) = không hạn chế; 5xx (máy chủ lỗi) hoặc 429 (đang giới hạn tốc độ — crawler
            # này dừng khi gặp 429) = coi như cấm hết
            if resp.status_code >= 500 or resp.status_code == 429:
                rules = RobotsRules(["User-agent: *", "Disallow: /"])
            else:
                rules = RobotsRules(resp.text.splitlines() if resp.status_code == 200 else [])
            self._robots[origin] = rules
            self._host_delay[parts.netloc] = max(self.min_delay, rules.crawl_delay(self.user_agent) or 0.0)
        return self._robots[origin]

    def _check(self, url: str) -> None:
        rules = self._robots_for(url)
        if not rules.can_fetch(self.user_agent, url):
            raise Disallowed(url)
        if (rules.crawl_delay(self.user_agent) or 0) > MAX_CRAWL_DELAY:
            raise Disallowed(f"{url} (robots Crawl-delay > {MAX_CRAWL_DELAY} giây)")

    def get(self, url: str) -> str:
        return self._request(url, None)

    def post_json(self, url: str, body: dict) -> str:
        """POST JSON (API tìm kiếm công khai mà chính trang web gọi, không cần đăng nhập — VietnamWorks). Cùng quy tắc
        với GET: robots.txt, giãn cách, thử lại lỗi tạm thời, dừng khi bị chặn."""
        return self._request(url, body)

    def _request(self, url: str, body: dict | None) -> str:
        self._check(url)
        for attempt in range(self.max_retries + 1):
            last_try = attempt >= self.max_retries
            backoff = self.min_delay * (2 ** (attempt + 1))
            # Lỗi tạm thời phía máy chủ/mạng (timeout, mất kết nối, 502/504 — không phải tín hiệu chặn): nghỉ lâu dần
            # rồi thử lại; hết lượt → ném lỗi thường để bộ chạy bỏ qua đúng trang đó (Việc Làm 24h 11/11: 1 ReadTimeout
            # từng làm dừng cả 14.700 trang sitemap còn lại).
            try:
                resp = self._fetch_following_redirects(url, body)
            except httpx.TransportError:
                if last_try:
                    raise
                time.sleep(backoff)
                continue
            if resp.status_code in (502, 504) and not last_try:
                time.sleep(backoff)
                continue
            body = resp.text
            if is_challenge(resp.status_code, body):
                raise Blocked(f"{resp.status_code} challenge at {url}")
            # 429 = nguồn yêu cầu giảm tốc → dừng ngay (không thử lại trong cùng lần chạy)
            if resp.status_code in (403, 429):
                raise Blocked(f"{resp.status_code} at {url}")
            if resp.status_code == 503:
                wait = _retry_after(resp)
                wait = self.min_delay * (2 ** (attempt + 1)) if wait is None else wait
                if wait > 300 or attempt >= self.max_retries:  # nghỉ lâu / hết lượt thử → dừng, lần chạy sau thử lại
                    raise Blocked(f"503 at {url} (Retry-After {wait:.0f}s, lần thử {attempt + 1})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return body
        raise Blocked(f"retries exhausted at {url}")  # không tới được: vòng lặp luôn return/raise

    def _fetch_following_redirects(self, url: str, body: dict | None = None) -> httpx.Response:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            self._wait_turn(urlsplit(current).netloc)
            resp = self._client.get(current) if body is None else self._client.post(current, json=body)
            self.requests_made += 1
            location = resp.headers.get("location")
            if not (resp.is_redirect and location):
                return resp
            current = urljoin(current, location)
            if urlsplit(current).scheme not in ("http", "https"):
                raise Blocked(f"chuyển hướng sang giao thức không hỗ trợ: {current[:80]}")
            self._check(current)  # đích chuyển hướng cũng phải được robots.txt cho phép
        raise Blocked(f"quá {MAX_REDIRECTS} lần chuyển hướng từ {url}")


def _retry_after(resp: httpx.Response) -> float | None:
    """Giây phải chờ theo Retry-After (số giây hoặc ngày giờ HTTP); không hợp lệ → None. Không bao giờ âm."""
    value = resp.headers.get("retry-after", "").strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            moment = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if moment.tzinfo is None:
            return None
        seconds = moment.timestamp() - time.time()
    return max(0.0, seconds) if math.isfinite(seconds) else None


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
