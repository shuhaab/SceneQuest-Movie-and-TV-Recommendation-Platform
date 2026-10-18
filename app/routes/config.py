"""Public app configuration."""

from flask import Blueprint, current_app, g, jsonify, request

from app.tmdb.genres import mapping_for_lists
from app.tmdb.control import RequestBudget

config_bp = Blueprint("config", __name__, url_prefix="/api/v1")


@config_bp.get("/config")
def get_config():
    language = request.args.get("display_language", "en-US")
    if language != "en-US":
        return jsonify(error={"code": "INVALID_FILTER", "message": "Invalid display language.",
                              "fields": {"display_language": "Only en-US is supported currently."}},
                       request_id=g.request_id), 422

    cache = current_app.extensions["public_cache"]
    cache_key = ("config", language)
    result = cache.get(cache_key)
    if result is None:
        tmdb = current_app.extensions["tmdb_client"]
        budget = RequestBudget(8)
        movies = tmdb.genres("movie", language, budget=budget)
        tv = tmdb.genres("tv", language, budget=budget)
        result = {
            "schema_version": 1,
            "defaults": {"region": "MY", "display_language": "en-US", "access_type": "flatrate"},
            "supported_regions": [{"code": "MY", "label": "Malaysia"},
                                  {"code": "US", "label": "United States"},
                                  {"code": "GB", "label": "United Kingdom"}],
            "supported_display_languages": ["en-US"],
            "genres": mapping_for_lists(movies, tv),
            "limits": {"recommendations": 3, "batch_titles": 20,
                       "exclusions": 1000, "provider_choices": 10},
        }
        cache.set(cache_key, result, 24 * 60 * 60)
    response = jsonify(result)
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response
