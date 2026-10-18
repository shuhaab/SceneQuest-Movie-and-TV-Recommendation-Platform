"""Validation for the public catalogue API."""

import math
import re

from app.tmdb.genres import GENRES
from werkzeug.datastructures import MultiDict

REGIONS = {"MY", "US", "GB"}
ACCESS_TYPES = {"flatrate", "free", "ads", "rent", "buy"}


class ValidationError(Exception):
    def __init__(self, fields):
        self.fields = fields
        super().__init__("Invalid request fields.")


def catalogue_query(args, kind):
    common = {"media_type", "display_language"}
    allowed = {
        "search": common | {"q", "page"},
        "discover": common | {"page", "region", "genre_keys", "provider_ids", "access_type",
                              "rating_min", "rating_max", "year_from", "year_to",
                              "original_language", "runtime_min", "runtime_max"},
        "details": {"region", "display_language"},
        "providers": common | {"region"},
    }[kind]
    errors = {key: "Unknown parameter." for key in args if key not in allowed}
    for key in args:
        if len(args.getlist(key)) > 1:
            errors[key] = "Supply this parameter once."
    result = {"display_language": args.get("display_language", "en-US")}
    if result["display_language"] != "en-US":
        errors["display_language"] = "Only en-US is supported currently."
    if kind != "details":
        result["media_type"] = args.get("media_type", "")
        if result["media_type"] not in {"movie", "tv"}:
            errors["media_type"] = "Choose movie or tv."
    if "region" in allowed:
        result["region"] = args.get("region", "MY")
        if result["region"] not in REGIONS:
            errors["region"] = "Choose MY, US, or GB."

    def number(key, low, high, *, default=None, integer=True):
        value = args.get(key)
        if value is None:
            if default is not None:
                result[key] = default
            return
        try:
            if integer and not re.fullmatch(r"[0-9]+", value):
                raise ValueError
            parsed = int(value) if integer else float(value)
            if not math.isfinite(parsed) or not low <= parsed <= high:
                raise ValueError
            result[key] = parsed
        except (ValueError, OverflowError):
            errors[key] = f"Must be {'an integer' if integer else 'a number'} from {low} to {high}."

    if "page" in allowed:
        number("page", 1, 500, default=1)
    if kind == "search":
        result["q"] = args.get("q", "").strip()
        if not 1 <= len(result["q"]) <= 200:
            errors["q"] = "Enter a title of 1 to 200 characters."
    if kind == "discover":
        for key in ("rating_min", "rating_max"):
            number(key, 0, 10, integer=False)
        for key in ("year_from", "year_to"):
            number(key, 1870, 2100)
        for key in ("runtime_min", "runtime_max"):
            number(key, 1, 1000)
        for lower, upper in (("rating_min", "rating_max"), ("year_from", "year_to"),
                             ("runtime_min", "runtime_max")):
            if lower in result and upper in result and result[lower] > result[upper]:
                errors[upper] = f"Must be at least {lower}."
        result["access_type"] = args.get("access_type", "flatrate")
        if result["access_type"] not in ACCESS_TYPES:
            errors["access_type"] = "Choose flatrate, free, ads, rent, or buy."
        if "original_language" in args:
            result["original_language"] = args["original_language"]
            if not re.fullmatch(r"[a-z]{2}", result["original_language"]):
                errors["original_language"] = "Use a two-letter language code."
        result["genre_keys"] = sorted(set(args.get("genre_keys", "").split(","))) if args.get("genre_keys") else []
        for key in result["genre_keys"]:
            if key not in GENRES or result["media_type"] not in GENRES[key][1]:
                errors["genre_keys"] = "A selected genre is unsupported for this media type."
        raw_providers = args.get("provider_ids", "")
        result["provider_ids"] = []
        if raw_providers:
            parts = raw_providers.split(",")
            if len(parts) > 10 or any(len(item) > 10 or not re.fullmatch(r"[0-9]+", item) or not 0 < int(item) <= 2147483647 for item in parts):
                errors["provider_ids"] = "Choose up to 10 positive provider IDs."
            else:
                result["provider_ids"] = sorted({int(item) for item in parts})
    if errors:
        raise ValidationError(errors)
    return result


def batch_request(body):
    if not isinstance(body, dict):
        raise ValidationError({"body": "Send a JSON object."})
    errors = {key: "Unknown field." for key in body if key not in {"titles", "region", "display_language"}}
    titles = body.get("titles")
    if not isinstance(titles, list) or not 1 <= len(titles) <= 20:
        errors["titles"] = "Send from 1 to 20 title identities."
    identities, seen = [], set()
    if isinstance(titles, list) and len(titles) <= 20:
        for i, item in enumerate(titles):
            if (not isinstance(item, dict) or set(item) != {"media_type", "tmdb_id"}
                    or not isinstance(item.get("media_type"), str)
                    or item.get("media_type") not in {"movie", "tv"}
                    or not isinstance(item.get("tmdb_id"), int) or isinstance(item.get("tmdb_id"), bool)
                    or not 0 < item["tmdb_id"] <= 2147483647):
                errors[f"titles.{i}"] = "Use a positive movie or tv title identity."
                continue
            key = (item["media_type"], item["tmdb_id"])
            if key not in seen:
                identities.append(item)
                seen.add(key)
    if errors:
        raise ValidationError(errors)
    params = {key: body[key] for key in ("region", "display_language") if key in body}
    if any(not isinstance(value, str) for value in params.values()):
        raise ValidationError({"body": "Country and display language must be strings."})
    return identities, catalogue_query(MultiDict(params), "details")
