"""Slows down password guessing: after 5 failed logins for an email from one address within
15 minutes, further attempts are refused until the window passes.

Kept in memory, which suits a single API process (one lab server). Several processes would
need a shared store such as Redis.
"""

import time
from collections import defaultdict, deque

MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60


class LoginLimiter:
    def __init__(self, max_failures: int = MAX_FAILURES, window: float = WINDOW_SECONDS) -> None:
        self.max_failures = max_failures
        self.window = window
        self._failures: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def _recent(self, key: tuple[str, str]) -> deque[float]:
        attempts = self._failures[key]
        cutoff = time.monotonic() - self.window
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        return attempts

    def retry_after(self, email: str, ip: str) -> int:
        """Seconds until another attempt is allowed; 0 if allowed now."""
        attempts = self._recent((email, ip))
        if len(attempts) < self.max_failures:
            return 0
        return max(1, int(attempts[0] + self.window - time.monotonic()))

    def failed(self, email: str, ip: str) -> None:
        self._recent((email, ip)).append(time.monotonic())

    def succeeded(self, email: str, ip: str) -> None:
        self._failures.pop((email, ip), None)

    def reset(self) -> None:
        self._failures.clear()


login_limiter = LoginLimiter()
