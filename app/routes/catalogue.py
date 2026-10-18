"""Versioned public catalogue endpoints."""

from flask import Blueprint, current_app, g, jsonify, request

from app.schemas import ValidationError, catalogue_query, batch_request
from werkzeug.exceptions import BadRequest
from app.services.catalogue import CatalogueService

catalogue_bp = Blueprint("catalogue", __name__, url_prefix="/api/v1")


def service():
    return CatalogueService(current_app.extensions["tmdb_client"], current_app.extensions["public_cache"])


def respond(result, *, stale=False, cache_seconds=60):
    result["request_id"] = g.request_id
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store" if stale else f"public, max-age={cache_seconds}"
    return response


@catalogue_bp.get("/search")
def search():
    return respond(service().search(catalogue_query(request.args, "search")), cache_seconds=600)


@catalogue_bp.get("/discover")
def discover():
    result = service().discover(catalogue_query(request.args, "discover"))
    return respond(result, stale=not result["verification_complete"])


@catalogue_bp.get("/providers")
def providers():
    return respond(service().providers(catalogue_query(request.args, "providers")), cache_seconds=3600)


@catalogue_bp.get("/titles/<media>/<int:title_id>")
def details(media, title_id):
    if media not in {"movie", "tv"} or not 0 < title_id <= 2147483647:
        raise ValidationError({"title": "Use a positive movie or tv title ID."})
    result = service().details(media, title_id, catalogue_query(request.args, "details"))
    return respond(result, stale=bool(result["warnings"]))


@catalogue_bp.post("/titles/batch")
def batch():
    if not request.is_json:
        raise BadRequest("Send a JSON request body.")
    identities, filters = batch_request(request.get_json())
    return respond(service().batch(identities, filters), stale=True)
