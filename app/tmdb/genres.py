"""Explicit application keys for TMDB's separate movie and TV genre IDs."""

GENRES = {
    "action": ("Action", {"movie": 28}),
    "adventure": ("Adventure", {"movie": 12}),
    "action_adventure": ("Action & Adventure", {"tv": 10759}),
    "animation": ("Animation", {"movie": 16, "tv": 16}),
    "comedy": ("Comedy", {"movie": 35, "tv": 35}),
    "crime": ("Crime", {"movie": 80, "tv": 80}),
    "documentary": ("Documentary", {"movie": 99, "tv": 99}),
    "drama": ("Drama", {"movie": 18, "tv": 18}),
    "family": ("Family", {"movie": 10751, "tv": 10751}),
    "fantasy": ("Fantasy", {"movie": 14}),
    "history": ("History", {"movie": 36}),
    "horror": ("Horror", {"movie": 27}),
    "music": ("Music", {"movie": 10402}),
    "mystery": ("Mystery", {"movie": 9648, "tv": 9648}),
    "romance": ("Romance", {"movie": 10749}),
    "science_fiction": ("Science Fiction", {"movie": 878}),
    "sci_fi_fantasy": ("Sci-Fi & Fantasy", {"tv": 10765}),
    "thriller": ("Thriller", {"movie": 53}),
    "war": ("War", {"movie": 10752}),
    "western": ("Western", {"movie": 37, "tv": 37}),
    "tv_movie": ("TV Movie", {"movie": 10770}),
    "kids": ("Kids", {"tv": 10762}),
    "news": ("News", {"tv": 10763}),
    "reality": ("Reality", {"tv": 10764}),
    "soap": ("Soap", {"tv": 10766}),
    "talk": ("Talk", {"tv": 10767}),
    "war_politics": ("War & Politics", {"tv": 10768}),
}


def mapping_for_lists(movie_genres, tv_genres):
    """Include only explicitly mapped IDs that TMDB currently advertises."""
    available = {
        "movie": {item.get("id") for item in movie_genres if isinstance(item, dict) and type(item.get("id")) is int},
        "tv": {item.get("id") for item in tv_genres if isinstance(item, dict) and type(item.get("id")) is int},
    }
    return [
        {"key": key, "label": label,
         "tmdb_ids": {media: genre_id for media, genre_id in ids.items()
                      if genre_id in available[media]}}
        for key, (label, ids) in GENRES.items()
        if any(genre_id in available[media] for media, genre_id in ids.items())
    ]


def keys_for_ids(media_type, genre_ids):
    if media_type not in {"movie", "tv"}:
        raise ValueError("Unsupported media type")
    if not isinstance(genre_ids, list):
        return []
    known = {value for value in genre_ids if isinstance(value, int) and not isinstance(value, bool)}
    return [key for key, (_, ids) in GENRES.items()
            if media_type in ids and ids[media_type] in known]
