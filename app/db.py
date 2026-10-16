"""Shared asyncpg connection pool, created once at app startup."""
import json

import asyncpg

from app.config import settings

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    # jsonb columns (pending_confirmations.payload, turn_logs.tool_args/tool_result)
    # round-trip as native dicts instead of raw JSON strings.
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        if settings.cloud_sql_connection_name:
            # Cloud Run mounts the Cloud SQL Auth Proxy's socket here --
            # not reachable as a normal TCP host from Cloud Run.
            socket_dir = f"/cloudsql/{settings.cloud_sql_connection_name}"
            _pool = await asyncpg.create_pool(
                host=socket_dir,
                user=settings.db_user,
                password=settings.db_password,
                database=settings.db_name,
                min_size=1,
                max_size=10,
                init=_init_connection,
            )
        else:
            _pool = await asyncpg.create_pool(
                settings.database_url, min_size=1, max_size=10, init=_init_connection
            )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_pool() at startup first")
    return _pool
