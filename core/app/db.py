"""Shared asyncpg connection pool for nexus-core."""
import logging

import asyncpg

from app.config import get_settings

logger = logging.getLogger("nexus-core.db")

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    """Create the pool. Raises if the database is unreachable."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            **get_settings().db_kwargs(),
            min_size=1,
            max_size=10,
        )
        logger.info("database pool established")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("database pool closed")


def get_pool() -> asyncpg.Pool | None:
    return _pool
