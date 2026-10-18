"""Personal recommendation inputs are never publicly cached."""
from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.exceptions import BadRequest
from app.recommendation_schema import recommendation_request
from app.services.recommendations import RecommendationService

recommendations_bp = Blueprint('recommendations', __name__, url_prefix='/api/v1')


@recommendations_bp.post('/recommendations')
def recommendations():
    if not request.is_json:
        raise BadRequest('Send a JSON request body.')
    inputs = recommendation_request(request.get_json())
    service = RecommendationService(current_app.extensions['tmdb_client'], current_app.extensions['public_cache'])
    result, status = service.recommend(inputs)
    result['request_id'] = g.request_id
    response = jsonify(result)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    return response
