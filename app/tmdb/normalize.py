"""Convert movie and TV results to a common public title representation."""

from datetime import date, datetime, timezone
from statistics import median
from math import isfinite
import re

from app.tmdb.genres import keys_for_ids


def finite(value):
    try:
        return isfinite(value)
    except (OverflowError, TypeError):
        return False


def normalize_title(raw, media_type, *, checked_at=None):
    if media_type not in {"movie", "tv"}:
        raise ValueError("Unsupported media type")
    title_id = raw.get("id")
    if not isinstance(title_id, int) or isinstance(title_id, bool) or not 0 < title_id <= 2147483647:
        raise ValueError("TMDB title is missing a valid ID")
    genre_ids = raw.get("genre_ids")
    if not isinstance(genre_ids, list):
        genres = raw.get("genres")
        genre_ids = [genre.get("id") for genre in (genres if isinstance(genres, list) else [])
                     if isinstance(genre, dict)]
    average = raw.get("vote_average")
    votes = raw.get("vote_count")
    if (not isinstance(average, (int, float)) or isinstance(average, bool)
            or not 0 <= average <= 10):
        average = None
    if not isinstance(votes, int) or isinstance(votes, bool) or not 0 <= votes <= 9007199254740991:
        votes = None
    runtime_minutes = raw.get("runtime") if media_type == "movie" else None
    if media_type == "tv":
        episode_runtimes = raw.get("episode_run_time")
        if not isinstance(episode_runtimes, list):
            episode_runtimes = []
        valid = [value for value in episode_runtimes
                 if isinstance(value, int) and not isinstance(value, bool) and value > 0 and finite(value)]
        try:
            runtime_minutes = median(valid) if valid else None
        except OverflowError:
            runtime_minutes = None
    if (not isinstance(runtime_minutes, (int, float)) or isinstance(runtime_minutes, bool)
            or runtime_minutes <= 0 or not finite(runtime_minutes)):
        runtime_minutes = None
    release_date = raw.get("release_date" if media_type == "movie" else "first_air_date")
    try:
        release_date = date.fromisoformat(release_date).isoformat()
    except (ValueError, TypeError):
        release_date = None
    language = raw.get("original_language")
    if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2}", language):
        language = None
    poster = raw.get("poster_path")
    if not isinstance(poster, str) or not re.fullmatch(r"/[A-Za-z0-9_.-]+", poster):
        poster = None
    title = raw.get("title" if media_type == "movie" else "name")
    overview = raw.get("overview")
    return {
        "key": f"{media_type}:{title_id}",
        "media_type": media_type,
        "tmdb_id": title_id,
        "title": title if isinstance(title, str) and title else "Untitled",
        "overview": overview if isinstance(overview, str) else "",
        "poster_path": poster,
        "first_release_date": release_date,
        "original_language": language,
        "genre_keys": keys_for_ids(media_type, genre_ids),
        "rating": {"average": average, "votes": votes},
        "runtime": {"minutes": runtime_minutes, "basis": "movie" if media_type == "movie" else "tv_episode"},
        "metadata_checked_at": checked_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
