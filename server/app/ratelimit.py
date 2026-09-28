"""Small in-memory sliding-window rate limiter (single server process).

Used for login, sign-up and password reset. Replace with Redis when running several instances.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


class RateLimiter:
    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> None:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window_s:
            q.popleft()
        if len(q) >= self.limit:
            retry = int(self.window_s - (now - q[0])) + 1
            raise HTTPException(429, "too many attempts, try again later", headers={"Retry-After": str(retry)})
        q.append(now)

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


def client_ip(request: Request) -> str:
    # Behind Caddy the real client is in X-Forwarded-For (Caddy sets it; the app is not exposed directly).
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


LOGIN_PER_ACCOUNT = RateLimiter(5, 300)  # 5 failures / 5 min per email+ip
LOGIN_PER_IP = RateLimiter(30, 300)
SIGNUP_PER_IP = RateLimiter(10, 3600)
RESET_PER_EMAIL = RateLimiter(3, 3600)
