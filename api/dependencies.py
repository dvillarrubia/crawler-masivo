"""Shared dependencies for FastAPI endpoints."""

from __future__ import annotations

import redis as _redis

# Module-level Redis client, initialised by the app lifespan.
redis_client: _redis.Redis | None = None


def get_redis() -> _redis.Redis:
    """Return the module-level Redis client. Raises if not initialised."""
    if redis_client is None:
        raise RuntimeError("Redis client has not been initialised")
    return redis_client
