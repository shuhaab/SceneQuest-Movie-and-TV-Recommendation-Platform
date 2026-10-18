"""Catalogue retrieval, public caching, and hard-filter verification."""

from datetime import date, datetime, timezone
from urllib.parse import urlparse

from app.schemas import ACCESS_TYPES
from app.tmdb.control import RequestBudget
from app.tmdb.errors import TMDBError
from app.tmdb.genres import GENRES
from app.tmdb.normalize import normalize_title


def timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def discovery_params(filters):
    params = {"language": filters["display_language"], "page": filters["page"],
              "include_adult": "false", "sort_by": "popularity.desc"}
    media = filters["media_type"]
    if filters["genre_keys"]:
        params["with_genres"] = "|".join(str(GENRES[key][1][media]) for key in filters["genre_keys"])
    if filters["provider_ids"]:
        params.update(with_watch_providers="|".join(map(str, filters["provider_ids"])),
                      watch_region=filters["region"],
                      with_watch_monetization_types=filters["access_type"])
    for field, upstream in (("rating_min", "vote_average.gte"), ("rating_max", "vote_average.lte"),
                            ("original_language", "with_original_language")):
        if field in filters:
            params[upstream] = filters[field]
    year_prefix = "primary_release_date" if media == "movie" else "first_air_date"
    if "year_from" in filters:
        params[year_prefix + ".gte"] = f"{filters['year_from']}-01-01"
    if "year_to" in filters:
        params[year_prefix + ".lte"] = f"{filters['year_to']}-12-31"
    if media == "movie":
        for field, upstream in (("runtime_min", "with_runtime.gte"), ("runtime_max", "with_runtime.lte")):
            if field in filters:
                params[upstream] = filters[field]
    return params


def matches_basic(title, filters):
    if filters["genre_keys"] and not set(filters["genre_keys"]) & set(title["genre_keys"]):
        return False
    if "original_language" in filters and title["original_language"] != filters["original_language"]:
        return False
    average = title["rating"]["average"]
    if "rating_min" in filters and (average is None or average < filters["rating_min"]):
        return False
    if "rating_max" in filters and (average is None or average > filters["rating_max"]):
        return False
    if "year_from" in filters or "year_to" in filters:
        try:
            year = date.fromisoformat(title["first_release_date"]).year
        except (TypeError, ValueError):
            return False
        if "year_from" in filters and year < filters["year_from"]:
            return False
        if "year_to" in filters and year > filters["year_to"]:
            return False
    return True


def matches_runtime(title, filters):
    runtime = title["runtime"]["minutes"]
    if "runtime_min" in filters and (runtime is None or runtime < filters["runtime_min"]):
        return False
    if "runtime_max" in filters and (runtime is None or runtime > filters["runtime_max"]):
        return False
    return True


class CatalogueService:
    def __init__(self, tmdb, cache):
        self.tmdb = tmdb
        self.cache = cache

    def _fetch(self, key, loader, ttl, *, allow_stale=False):
        cached = self.cache.lookup(key)
        if cached is not None and not cached[1]:
            data, checked_at = cached[0]
            return data, checked_at, False
        try:
            data = loader()
        except TMDBError as error:
            if allow_stale and cached is not None and error.code != "UPSTREAM_NOT_FOUND":
                data, checked_at = cached[0]
                return data, checked_at, True
            raise
        checked_at = timestamp()
        self.cache.set(key, (data, checked_at), ttl)
        return data, checked_at, False

    @staticmethod
    def _page(raw):
        if (not isinstance(raw, dict) or not isinstance(raw.get("results"), list)
                or len(raw["results"]) > 20
                or type(raw.get("total_pages")) is not int
                or raw["total_pages"] < 0):
            raise TMDBError("UPSTREAM_RESPONSE_INVALID", "TMDB returned invalid catalogue data.")
        return raw["results"]

    @staticmethod
    def _normalize(raw, media, checked_at, title_id=None):
        try:
            if not isinstance(raw, dict):
                raise ValueError
            title = normalize_title(raw, media, checked_at=checked_at)
            if title_id is not None and title["tmdb_id"] != title_id:
                raise ValueError
            return title
        except (ValueError, TypeError) as exc:
            raise TMDBError("UPSTREAM_RESPONSE_INVALID", "TMDB returned invalid title data.") from exc

    def search(self, filters):
        budget = RequestBudget(8)
        media = filters["media_type"]
        params = {"query": filters["q"], "page": filters["page"],
                  "language": filters["display_language"], "include_adult": "false"}
        key = ("search", media, tuple(sorted(params.items())))
        raw, checked_at, _ = self._fetch(key, lambda: self.tmdb.search(media, params, budget=budget), 600)
        results = []
        warnings = []
        for item in self._page(raw):
            if isinstance(item, dict) and item.get("adult") is True:
                continue
            try:
                results.append(self._normalize(item, media, checked_at))
            except TMDBError:
                warnings.append("An invalid upstream title was omitted.")
        return {"results": results, "page": filters["page"], "total_pages": min(raw["total_pages"], 500),
                "total_results": raw.get("total_results"), "warnings": sorted(set(warnings))}

    def _detail(self, media, title_id, language, budget, *, allow_stale=False):
        raw, checked_at, stale = self._fetch(
            ("details", media, title_id, language),
            lambda: self.tmdb.details(media, title_id, language, budget=budget),
            86400, allow_stale=allow_stale)
        title = self._normalize(raw, media, checked_at, title_id)
        if raw.get("adult") is True:
            raise TMDBError("UPSTREAM_NOT_FOUND", "Title was not found.")
        title["metadata_stale"] = stale
        return title

    def _availability(self, media, title_id, region, budget, *, allow_stale=False):
        raw, checked_at, stale = self._fetch(
            ("availability", media, title_id, region),
            lambda: self.tmdb.availability(media, title_id, budget=budget),
            21600, allow_stale=allow_stale)
        if not isinstance(raw, dict) or not isinstance(raw.get("results"), dict):
            raise TMDBError("UPSTREAM_RESPONSE_INVALID", "TMDB returned invalid provider data.")
        regional = raw["results"].get(region, {})
        if not isinstance(regional, dict):
            raise TMDBError("UPSTREAM_RESPONSE_INVALID", "TMDB returned invalid provider data.")
        providers = {}
        for mode in sorted(ACCESS_TYPES):
            items = regional.get(mode, [])
            if not isinstance(items, list):
                raise TMDBError("UPSTREAM_RESPONSE_INVALID", "TMDB returned invalid provider data.")
            providers[mode] = self._provider_items(items)
        link = regional.get("link")
        try:
            parsed = urlparse(link) if isinstance(link, str) else None
        except ValueError:
            parsed = None
        if parsed is None or parsed.scheme != "https" or parsed.hostname != "www.themoviedb.org":
            link = None
        return {"region": region, "providers": providers, "watch_url": link,
                "checked_at": checked_at, "stale": stale, "source": "TMDB / JustWatch"}

    @staticmethod
    def _provider_items(items):
        result = []
        for item in items:
            if (not isinstance(item, dict) or not isinstance(item.get("provider_id"), int)
                    or isinstance(item["provider_id"], bool) or item["provider_id"] <= 0
                    or not isinstance(item.get("provider_name"), str)):
                raise TMDBError("UPSTREAM_RESPONSE_INVALID", "TMDB returned invalid provider data.")
            result.append({"id": item["provider_id"], "name": item["provider_name"],
                           "logo_path": item.get("logo_path")})
        return result

    def details(self, media, title_id, filters, *, budget=None):
        budget = budget or RequestBudget(4)
        title = self._detail(media, title_id, filters["display_language"], budget, allow_stale=True)
        warnings = ["Displaying stale title metadata."] if title["metadata_stale"] else []
        try:
            availability = self._availability(media, title_id, filters["region"], budget, allow_stale=True)
            if availability["stale"]:
                warnings.append("Displaying stale availability.")
        except TMDBError:
            availability = {"region": filters["region"], "status": "unavailable", "checked_at": None}
            warnings.append("Regional availability is temporarily unavailable.")
        title["availability"] = availability
        title["warnings"] = warnings
        return title

    def batch(self, identities, filters):
        budget = RequestBudget(40)
        titles, errors = [], []
        for identity in identities:
            media, title_id = identity["media_type"], identity["tmdb_id"]
            try:
                titles.append(self.details(media, title_id, filters, budget=budget))
            except TMDBError as error:
                errors.append({"key": f"{media}:{title_id}", "code": error.code, "message": error.message})
        return {"titles": titles, "errors": errors}

    def providers(self, filters):
        budget = RequestBudget(8)
        media, region, language = filters["media_type"], filters["region"], filters["display_language"]
        raw, checked_at, _ = self._fetch(
            ("providers", media, region, language),
            lambda: self.tmdb.providers(media, region, language, budget=budget), 86400)
        if not isinstance(raw, dict) or not isinstance(raw.get("results"), list):
            raise TMDBError("UPSTREAM_RESPONSE_INVALID", "TMDB returned invalid provider data.")
        return {"media_type": media, "region": region,
                "providers": self._provider_items(raw["results"]), "checked_at": checked_at}

    def discover(self, filters):
        budget = RequestBudget(41)
        media = filters["media_type"]
        params = discovery_params(filters)
        raw, checked_at, _ = self._fetch(
            ("discover", media, tuple(sorted(params.items()))),
            lambda: self.tmdb.discover(media, params, budget=budget), 600)
        results, warnings, seen = [], [], set()
        runtime_active = "runtime_min" in filters or "runtime_max" in filters
        for item in self._page(raw):
            if isinstance(item, dict) and item.get("adult") is True:
                continue
            try:
                title = self._normalize(item, media, checked_at)
                if title["key"] in seen:
                    continue
                seen.add(title["key"])
                if not matches_basic(title, filters):
                    continue
                if runtime_active:
                    title = self._detail(media, title["tmdb_id"], filters["display_language"], budget)
                    if not matches_basic(title, filters) or not matches_runtime(title, filters):
                        continue
                if filters["provider_ids"]:
                    availability = self._availability(media, title["tmdb_id"], filters["region"], budget)
                    verified_ids = {item["id"] for item in availability["providers"][filters["access_type"]]}
                    if not verified_ids & set(filters["provider_ids"]):
                        continue
                    title["availability"] = availability
                results.append(title)
            except TMDBError as error:
                warnings.append("Some titles could not be verified and were omitted.")
                if error.code == "WORK_LIMIT_REACHED":
                    break
        return {"results": results, "source_page": filters["page"],
                "has_more_source_pages": filters["page"] < min(raw["total_pages"], 500),
                "verification_complete": not warnings, "warnings": sorted(set(warnings))}
