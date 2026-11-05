from collections import defaultdict, deque
from time import monotonic

from fastapi import HTTPException, Request

MAX_RATE_LIMIT_KEYS = 20_000
_rate_limits: dict[str, deque[float]] = defaultdict(deque)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "local"


def _purge_rate_limits(now: float) -> None:
    # Windows are at most one hour; anything older is dead weight.
    for key in [k for k, bucket in _rate_limits.items() if not bucket or now - bucket[-1] > 3600]:
        del _rate_limits[key]
    if len(_rate_limits) > MAX_RATE_LIMIT_KEYS:
        _rate_limits.clear()


def rate_limit(key: str, limit: int, window_seconds: int) -> None:
    """Sliding-window limiter. Memory stays bounded: stale keys are purged when the table grows."""
    now = monotonic()
    if len(_rate_limits) > MAX_RATE_LIMIT_KEYS // 2:
        _purge_rate_limits(now)
    bucket = _rate_limits[key]
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(status_code=429, detail="Слишком много действий. Попробуйте позже.")
    bucket.append(now)
