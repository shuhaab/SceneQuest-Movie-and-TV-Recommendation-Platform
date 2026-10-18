"""SceneQuest application factory."""

import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

from flask import Flask, g, jsonify, render_template, request
from dotenv import load_dotenv
from werkzeug.exceptions import HTTPException

from app.cache import TTLCache
from app.rate_limit import RateLimiter
from app.routes.config import config_bp
from app.routes.catalogue import catalogue_bp
from app.routes.recommendations import recommendations_bp
from app.schemas import ValidationError
from app.tmdb.client import TMDBClient, TMDBError
from app.tmdb.genres import GENRES

ENV_FILE = Path(__file__).resolve().parent.parent / '.env'

def create_app(*, tmdb_client=None):
    load_dotenv(ENV_FILE, override=False)
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    app.config["ENABLE_RATE_LIMITS"] = os.getenv("SCENEQUEST_RATE_LIMITS", "0") == "1"
    app.extensions["rate_limiter"] = RateLimiter()
    app.extensions["tmdb_client"] = tmdb_client or TMDBClient()
    app.extensions["public_cache"] = TTLCache()
    app.register_blueprint(config_bp)
    app.register_blueprint(catalogue_bp)
    app.register_blueprint(recommendations_bp)

    @app.before_request
    def identify_request():
        g.request_id = str(uuid4())
        g.started_at = monotonic()
        if app.config['ENABLE_RATE_LIMITS'] and request.path.startswith('/api/v1/'):
            category = 'recommendations' if request.endpoint == 'recommendations.recommendations' else 'catalogue'
            retry = app.extensions['rate_limiter'].check(request.remote_addr or 'unknown', category, 10 if category == 'recommendations' else 60)
            if retry is not None:
                response = jsonify(error={'code': 'RATE_LIMITED', 'message': 'Too many requests. Please try again shortly.', 'fields': {}}, request_id=g.request_id)
                response.status_code = 429
                response.headers['Retry-After'] = str(retry)
                return response

    @app.after_request
    def attach_request_id(response):
        response.headers["X-Request-ID"] = g.request_id
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' https://image.tmdb.org; connect-src 'self'; "
            "base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
        if response.status_code >= 400:
            response.headers["Cache-Control"] = "no-store"
        # Endpoint names and timings only: no URLs, query values, bodies, or credentials.
        app.logger.info('request id=%s endpoint=%s status=%s duration_ms=%.1f',
                        g.request_id, request.endpoint or 'unmatched', response.status_code,
                        (monotonic() - g.started_at) * 1000)
        return response

    @app.get("/health")
    def health():
        response = jsonify(status="ok")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        genres = [{"key": key, "label": label, "tmdb_ids": ids}
                  for key, (label, ids) in GENRES.items()]
        return render_template("index.html", bootstrap={"genres": genres})

    @app.errorhandler(TMDBError)
    def tmdb_error(error):
        status = (404 if error.code == "UPSTREAM_NOT_FOUND" else
                  503 if error.code in {"TOKEN_MISSING", "TOKEN_INVALID", "UPSTREAM_UNAVAILABLE", "WORK_LIMIT_REACHED"} else 502)
        return jsonify(error={"code": error.code, "message": error.message, "fields": {}},
                       request_id=g.request_id), status

    @app.errorhandler(ValidationError)
    def validation_error(error):
        return jsonify(error={"code": "INVALID_FILTER", "message": "Invalid request fields.",
                              "fields": error.fields}, request_id=g.request_id), 422

    @app.errorhandler(HTTPException)
    def http_error(error):
        codes = {400: "BAD_REQUEST", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED",
                 413: "REQUEST_TOO_LARGE", 429: "RATE_LIMITED"}
        return jsonify(error={"code": codes.get(error.code, "HTTP_ERROR"),
                              "message": error.description, "fields": {}},
                       request_id=g.request_id), error.code

    return app
