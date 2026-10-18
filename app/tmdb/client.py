"""Allowlisted, server-side access to TMDB."""

import os
import time
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import BoundedSemaphore

import requests
from requests.adapters import HTTPAdapter

from app.tmdb.control import RequestBudget
from app.tmdb.errors import TMDBError


_UPSTREAM_SLOTS = BoundedSemaphore(4)
_BASE_URL = "https://api.themoviedb.org/3"


class TMDBClient:
    def __init__(self, token=None, session=None):
        self._token = token if token is not None else os.getenv("TMDB_ACCESS_TOKEN", "")
        self._session = session or requests.Session()
        if session is None:
            self._session.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=4))

    def _get(self, path, *, params=None, budget=None):
        if not self._token:
            raise TMDBError("TOKEN_MISSING", "TMDB access is not configured.")
        # Callers construct paths from fixed endpoints and validated media types.
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        budget = budget or RequestBudget(8)
        for attempt in range(2):
            budget.check()
            acquired = _UPSTREAM_SLOTS.acquire(timeout=budget.remaining())
            if not acquired:
                raise TMDBError("WORK_LIMIT_REACHED", "The request work budget was reached.")
            try:
                budget.consume()
                remaining = budget.remaining()
                response = self._session.get(
                    _BASE_URL + path, params=params, headers=headers,
                    timeout=(min(2, remaining / 3), min(4, remaining * 2 / 3))
                )
            except requests.RequestException as exc:
                if attempt == 0:
                    continue
                raise TMDBError("UPSTREAM_UNAVAILABLE", "TMDB is temporarily unavailable.") from exc
            finally:
                _UPSTREAM_SLOTS.release()
            if response.status_code in (401, 403):
                raise TMDBError("TOKEN_INVALID", "TMDB rejected the server credential. Check the API Read Access Token and restart Flask.")
            if response.status_code == 404:
                raise TMDBError("UPSTREAM_NOT_FOUND", "TMDB resource was not found.")
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt == 1:
                    break
                retry_after = response.headers.get("Retry-After")
                delay = 0
                if retry_after is not None:
                    try:
                        delay = float(retry_after)
                        if not math.isfinite(delay):
                            break
                        delay = max(0, delay)
                    except ValueError:
                        try:
                            delay = max(0, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                        except (TypeError, ValueError):
                            break
                if delay >= budget.remaining() - 0.05:
                    break
                if not math.isfinite(delay):
                    break
                budget.check()
                if delay:
                    time.sleep(delay)
                continue
            if not response.ok:
                raise TMDBError("UPSTREAM_ERROR", "TMDB could not complete the request.")
            try:
                data = response.json()
            except ValueError as exc:
                raise TMDBError("UPSTREAM_RESPONSE_INVALID", "TMDB returned invalid data.") from exc
            if not isinstance(data, dict):
                raise TMDBError("UPSTREAM_RESPONSE_INVALID", "TMDB returned invalid data.")
            return data
        raise TMDBError("UPSTREAM_UNAVAILABLE", "TMDB is temporarily unavailable.")

    def genres(self, media_type, language="en-US", *, budget=None):
        if media_type not in {"movie", "tv"}:
            raise ValueError("Unsupported media type")
        data = self._get(f"/genre/{media_type}/list", params={"language": language}, budget=budget)
        if not isinstance(data.get("genres"), list):
            raise TMDBError("UPSTREAM_RESPONSE_INVALID", "TMDB returned invalid genre data.")
        return data["genres"]

    @staticmethod
    def _media(media_type):
        if media_type not in {"movie", "tv"}:
            raise ValueError("Unsupported media type")
        return media_type

    @staticmethod
    def _title_id(title_id):
        if isinstance(title_id, bool) or not isinstance(title_id, int) or not 0 < title_id <= 2147483647:
            raise ValueError("Invalid title ID")
        return title_id

    def search(self, media_type, params, *, budget):
        return self._get(f"/search/{self._media(media_type)}", params=params, budget=budget)

    def discover(self, media_type, params, *, budget):
        return self._get(f"/discover/{self._media(media_type)}", params=params, budget=budget)

    def details(self, media_type, title_id, language, *, budget):
        return self._get(f"/{self._media(media_type)}/{self._title_id(title_id)}",
                         params={"language": language}, budget=budget)

    def availability(self, media_type, title_id, *, budget):
        return self._get(f"/{self._media(media_type)}/{self._title_id(title_id)}/watch/providers", budget=budget)

    def providers(self, media_type, region, language, *, budget):
        return self._get(f"/watch/providers/{self._media(media_type)}",
                         params={"watch_region": region, "language": language}, budget=budget)
