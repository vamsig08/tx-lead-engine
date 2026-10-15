"""HTTP with the spec's retry policy: 3 retries with exponential backoff."""
import logging
import time
from collections.abc import Callable

import httpx

log = logging.getLogger(__name__)

RETRIES = 3
BACKOFF_BASE_SECONDS = 1.0
RETRY_STATUSES = {429, 500, 502, 503, 504}


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    **kwargs,
) -> httpx.Response:
    """Send a request, retrying transport errors and 429/5xx up to RETRIES times.

    Waits 1s, 2s, 4s between attempts. Raises httpx.HTTPStatusError for a final
    non-2xx response, or the last transport error.
    """
    for attempt in range(RETRIES + 1):
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            if attempt == RETRIES:
                raise
            log.warning("%s %s failed (%s), retrying", method, url, exc)
        else:
            if response.status_code not in RETRY_STATUSES or attempt == RETRIES:
                response.raise_for_status()
                return response
            log.warning("%s %s returned %s, retrying", method, url, response.status_code)
        sleep(BACKOFF_BASE_SECONDS * 2**attempt)
    raise AssertionError("unreachable")
