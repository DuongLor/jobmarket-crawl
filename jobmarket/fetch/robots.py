"""robots.txt theo RFC 9309 — thay `urllib.robotparser` (không hiểu ký tự đại diện `*` và `$`).

`urllib.robotparser` coi `Disallow: /*?page=` là tiền tố chữ "/*?page=" → `/a?page=2` bị coi là ĐƯỢC PHÉP (review
2026-11-04). Ở đây:
- Nhóm theo `User-agent`: nhóm có tên khớp sản phẩm của bot (không phân biệt hoa thường) thắng nhóm `*`; nhiều nhóm
  cùng khớp thì gộp quy tắc.
- Quy tắc khớp với đường dẫn + query; `*` = chuỗi bất kỳ, `$` cuối = hết URL.
- Quy tắc khớp DÀI NHẤT thắng; bằng nhau thì Allow thắng. `Disallow:` rỗng = không cấm gì. `/robots.txt` luôn được phép.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)


def _pattern_regex(pattern: str) -> re.Pattern:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile(regex + ("$" if anchored else ""))


def _normalize(path: str) -> str:
    # So sánh trên dạng đã giải mã %XX (trừ "/" mã hoá) để "/vi%2Djobs" và "/vi-jobs" như nhau
    return unquote(path.replace("%2F", "%252F").replace("%2f", "%252F"))


class RobotsRules:
    def __init__(self, lines: list[str]):
        self.groups: list[_Group] = []
        current: _Group | None = None
        last_was_agent = False
        for raw in lines:
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if current is None or not last_was_agent:
                    current = _Group()
                    self.groups.append(current)
                current.agents.append(value.lower())
                last_was_agent = True
            elif key in ("allow", "disallow") and current is not None:
                last_was_agent = False
                if value:
                    current.rules.append((key == "allow", _normalize(value)))
            else:
                last_was_agent = False

    def _rules_for(self, user_agent: str) -> list[tuple[bool, str]]:
        product = re.split(r"[/\s]", user_agent.strip(), maxsplit=1)[0].lower()
        named = [g for g in self.groups if product in g.agents]
        chosen = named or [g for g in self.groups if "*" in g.agents]
        return [rule for group in chosen for rule in group.rules]

    def can_fetch(self, user_agent: str, url: str) -> bool:
        parts = urlsplit(url)
        path = _normalize((parts.path or "/") + (f"?{parts.query}" if parts.query else ""))
        if path == "/robots.txt":
            return True
        best: tuple[int, bool] | None = None
        for allow, pattern in self._rules_for(user_agent):
            if _pattern_regex(pattern).match(path):
                key = (len(pattern), allow)
                if best is None or key > best:
                    best = key
        return True if best is None else best[1]
