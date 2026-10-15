"""Read-only client for Socrata open-data portals (data.texas.gov)."""
import os
import time
from collections.abc import Callable, Iterator

import httpx

from integrations.http import request_with_retry


class SocrataClient:
    def __init__(self, base_url: str, *, page_size: int = 1000, timeout: float = 60,
                 client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        headers = {}
        # Optional and free; raises Socrata's rate limits for unauthenticated callers.
        if token := os.environ.get("SOCRATA_APP_TOKEN"):
            headers["X-App-Token"] = token
        self.page_size = page_size
        self._sleep = sleep
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout, headers=headers)

    def query(self, dataset_id: str, where: str) -> Iterator[dict]:
        """Yield every row matching a SoQL $where clause, one page at a time."""
        offset = 0
        while True:
            params = {
                "$where": where,
                # :id gives a stable order, so paging does not skip or repeat rows.
                "$order": ":id",
                "$limit": self.page_size,
                "$offset": offset,
            }
            response = request_with_retry(
                self._client, "GET", f"/resource/{dataset_id}.json", params=params,
                sleep=self._sleep,
            )
            rows = response.json()
            yield from rows
            if len(rows) < self.page_size:
                return
            offset += self.page_size

    def close(self) -> None:
        self._client.close()
