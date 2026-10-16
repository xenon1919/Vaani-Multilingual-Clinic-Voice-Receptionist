"""Global protection against hitting the free-tier Gemini rate limits
while running many scenarios: a semaphore capping concurrent requests, and
retry with exponential backoff + jitter on rate-limit/transient errors."""
import asyncio
import logging
import random
from typing import Awaitable, Callable, TypeVar

from google.genai import errors

logger = logging.getLogger(__name__)

MAX_CONCURRENT_REQUESTS = 1  # Vertex AI's trial-project quota is much tighter than AI Studio's free tier
MAX_RETRIES = 8
BASE_DELAY_S = 3.0
RETRYABLE_CODES = {429, 500, 503}

_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

T = TypeVar("T")


async def call_with_limit(coro_fn: Callable[..., Awaitable[T]], *args, **kwargs) -> T:
    """Runs coro_fn(*args, **kwargs) under the global concurrency cap,
    retrying with exponential backoff on rate-limit/server errors."""
    async with _semaphore:
        last_exc = None
        for attempt in range(MAX_RETRIES):
            try:
                return await coro_fn(*args, **kwargs)
            except errors.APIError as exc:
                last_exc = exc
                code = getattr(exc, "code", None)
                if code not in RETRYABLE_CODES or attempt == MAX_RETRIES - 1:
                    raise
                delay = BASE_DELAY_S * (2 ** attempt) + random.uniform(0, 1)
                logger.warning(
                    "Gemini API error %s (code=%s), retrying in %.1fs (attempt %d/%d)",
                    exc, code, delay, attempt + 1, MAX_RETRIES,
                )
                await asyncio.sleep(delay)
        raise last_exc  # unreachable, but keeps type checkers happy
