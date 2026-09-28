"""加密货币各来源共用的下载。只用标准库。

Coin Metrics 对浏览器 User-Agent 会回 403，所以 JSON 接口用 urllib 默认头。
Farside 需要浏览器头，由 etf.py 自己传。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

TIMEOUT = 60
RETRIES = 3


class FetchError(Exception):
    pass


def get_bytes(url: str, headers: dict | None = None, timeout: int = TIMEOUT, retries: int = RETRIES) -> bytes:
    hdrs = {"Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last = FetchError(f"HTTP {exc.code}")
            if exc.code in {404, 451, 400}:
                break
        except (urllib.error.URLError, TimeoutError, OSError, FetchError) as exc:
            last = exc
        if attempt < retries:
            time.sleep(2 ** (attempt - 1))
    raise FetchError(str(last))


def get_json(url: str, headers: dict | None = None, timeout: int = TIMEOUT):
    raw = get_bytes(url, headers=headers, timeout=timeout)
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise FetchError(f"不是 JSON：{raw[:80]!r}") from exc
