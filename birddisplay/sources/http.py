"""One polite HTTP session, shared by every outbound call.

Explicit timeout on every request, bounded retries with exponential
backoff, and a descriptive User-Agent (which Wikimedia's policy requires
and eBird appreciates).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from ..config import HttpConfig

log = logging.getLogger(__name__)

# Worth retrying: the server is busy or briefly broken. Anything else
# (400 bad params, 403 bad key, 404 no such region) will fail identically
# on the second attempt, so we fail fast instead.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class FetchError(Exception):
    """A request failed after exhausting its retries."""


class HttpClient:
    def __init__(self, config: HttpConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": config.user_agent})

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        stream: bool = False,
    ) -> requests.Response:
        attempts = max(1, self.config.retries)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.config.timeout_seconds,
                    stream=stream,
                )
            except requests.RequestException as exc:
                last_error = exc
                log.warning("GET %s failed (attempt %d/%d): %s", url, attempt, attempts, exc)
            else:
                if response.status_code in RETRY_STATUS:
                    last_error = FetchError(
                        f"GET {url} returned HTTP {response.status_code}"
                    )
                    log.warning(
                        "GET %s returned %d (attempt %d/%d)",
                        url,
                        response.status_code,
                        attempt,
                        attempts,
                    )
                    response.close()
                elif not response.ok:
                    raise FetchError(
                        f"GET {url} returned HTTP {response.status_code}: "
                        f"{response.text[:200]}"
                    )
                else:
                    return response

            if attempt < attempts:
                delay = self.config.backoff_seconds * (2 ** (attempt - 1))
                log.debug("retrying %s in %.1fs", url, delay)
                time.sleep(delay)

        raise FetchError(f"GET {url} failed after {attempts} attempts") from last_error

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        response = self.get(url, params=params, headers=headers)
        try:
            return response.json()
        except ValueError as exc:
            raise FetchError(f"GET {url} did not return JSON: {exc}") from exc

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
