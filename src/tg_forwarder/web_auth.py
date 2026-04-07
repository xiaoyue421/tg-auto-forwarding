from __future__ import annotations

"""HTTP dashboard session store and login rate limiting (in-memory, single process)."""

import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass

SESSION_COOKIE_NAME = "tg_dashboard_session"


@dataclass(slots=True)
class _SessionRecord:
    expires_at: float


class DashboardSessionStore:
    """In-memory session tokens for the web dashboard (single-process uvicorn)."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = max(60, int(ttl_seconds))
        self._sessions: dict[str, _SessionRecord] = {}
        self._lock = threading.Lock()

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._purge_unlocked(now)
            self._sessions[token] = _SessionRecord(expires_at=now + float(self._ttl_seconds))
        return token

    def validate(self, token: str | None) -> bool:
        if not token:
            return False
        now = time.monotonic()
        with self._lock:
            self._purge_unlocked(now)
            rec = self._sessions.get(token)
            if rec is None:
                return False
            if rec.expires_at <= now:
                self._sessions.pop(token, None)
                return False
            return True

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def _purge_unlocked(self, now: float) -> None:
        dead = [k for k, v in self._sessions.items() if v.expires_at <= now]
        for k in dead:
            self._sessions.pop(k, None)


class LoginRateLimiter:
    """Simple per-client-IP sliding window for failed login attempts."""

    def __init__(self, max_failures: int, window_seconds: int) -> None:
        self._max_failures = max(1, int(max_failures))
        self._window_seconds = max(1, int(window_seconds))
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def is_blocked(self, client_ip: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._purge_ip_unlocked(client_ip, now)
            dq = self._failures.get(client_ip)
            return bool(dq) and len(dq) >= self._max_failures

    def record_failure(self, client_ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            dq = self._failures.setdefault(client_ip, deque())
            dq.append(now)
            self._purge_ip_unlocked(client_ip, now)

    def reset(self, client_ip: str) -> None:
        with self._lock:
            self._failures.pop(client_ip, None)

    def _purge_ip_unlocked(self, client_ip: str, now: float) -> None:
        dq = self._failures.get(client_ip)
        if not dq:
            return
        cutoff = now - float(self._window_seconds)
        while dq and dq[0] < cutoff:
            dq.popleft()
        if not dq:
            self._failures.pop(client_ip, None)
