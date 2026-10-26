"""Gateway account discovery (read-only).

Real connections are driven by runner.protocol; this module only lists
accounts that should be online.
"""
import asyncpg

from runner import sqlres


async def load_active_accounts(pool: asyncpg.Pool):
    return await pool.fetch(sqlres.load("active_accounts.sql"))
