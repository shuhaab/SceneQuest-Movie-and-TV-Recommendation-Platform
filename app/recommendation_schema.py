"""Strict JSON inputs for recommendations."""
import math
import re
from werkzeug.datastructures import MultiDict
from app.schemas import ValidationError, catalogue_query
from app.tmdb.genres import GENRES


def recommendation_request(body):
    errors = {}

    def obj(value, allowed, path):
        if not isinstance(value, dict):
            errors[path] = 'Send an object.'
            return {}
        for key in value:
            if key not in allowed:
                errors[f'{path}.{key}'] = 'Unknown field.'
        return value

    body = obj(body, {'filters', 'preferences', 'taste_profile', 'exclude'}, 'body')
    filters = obj(body.get('filters', {}), {'media_type', 'region', 'display_language', 'genre_keys',
                  'provider_ids', 'access_type', 'rating_min', 'rating_max', 'year_from', 'year_to',
                  'original_language', 'runtime_min', 'runtime_max'}, 'filters')
    media = filters.get('media_type', 'both')
    if not isinstance(media, str) or media not in {'movie', 'tv', 'both'}:
        errors['filters.media_type'] = 'Choose movie, tv, or both.'
        media = 'both'
    types = ['movie', 'tv'] if media == 'both' else [media]

    def strings(value, path, valid):
        if not isinstance(value, list) or len(value) > 100 or any(not isinstance(x, str) or not valid(x) for x in value):
            errors[path] = 'Select valid values.'
            return []
        return sorted(set(value))

    hard_genres = strings(filters.get('genre_keys', []), 'filters.genre_keys', lambda x: x in GENRES)
    providers = filters.get('provider_ids', [])
    if not isinstance(providers, list) or len(providers) > 10 or any(type(x) is not int or not 0 < x <= 2147483647 for x in providers):
        errors['filters.provider_ids'] = 'Select up to 10 positive provider IDs.'
        providers = []
    common = {}
    for key, value in filters.items():
        if key in {'media_type', 'genre_keys', 'provider_ids', 'runtime_min', 'runtime_max'}:
            continue
        if key in {'rating_min', 'rating_max', 'year_from', 'year_to'}:
            valid = type(value) in {int, float} and (type(value) is int or math.isfinite(value))
            if key.startswith('year'):
                valid = type(value) is int
            if not valid:
                errors[f'filters.{key}'] = 'Supply a finite number of the required type.'
                continue
        elif not isinstance(value, str):
            errors[f'filters.{key}'] = 'Supply a string.'
            continue
        common[key] = str(value)
    per_media = {}
    for kind in types:
        params = dict(common, media_type=kind, genre_keys=','.join(hard_genres), provider_ids=','.join(map(str, providers)))
        for key in ('runtime_min', 'runtime_max'):
            if key not in filters:
                continue
            limits = obj(filters[key], {'movie', 'tv_episode'}, f'filters.{key}')
            for dimension, value in limits.items():
                if type(value) is not int or not 1 <= value <= 1000:
                    errors[f'filters.{key}.{dimension}'] = 'Use integer minutes from 1 to 1000.'
            dimension = 'movie' if kind == 'movie' else 'tv_episode'
            if type(limits.get(dimension)) is int:
                params[key] = str(limits[dimension])
        try:
            per_media[kind] = catalogue_query(MultiDict(params), 'discover')
        except ValidationError as error:
            errors.update({f'filters.{key}': value for key, value in error.fields.items()})
    prefs = obj(body.get('preferences', {}), {'preferred_genres', 'preferred_original_languages'}, 'preferences')
    preferences = {
        'preferred_genres': strings(prefs.get('preferred_genres', []), 'preferences.preferred_genres', lambda x: x in GENRES),
        'preferred_original_languages': strings(prefs.get('preferred_original_languages', []), 'preferences.preferred_original_languages', lambda x: bool(re.fullmatch('[a-z]{2}', x)))
    }
    profile = obj(body.get('taste_profile', {}), {'algorithm_version', 'genre_affinity', 'language_affinity', 'feedback_count'}, 'taste_profile')
    if type(profile.get('algorithm_version', 1)) is not int or profile.get('algorithm_version', 1) != 1:
        errors['taste_profile.algorithm_version'] = 'Only version 1 is supported.'
    count = profile.get('feedback_count', 0)
    if type(count) is not int or not 0 <= count <= 2147483647:
        errors['taste_profile.feedback_count'] = 'Use a nonnegative integer.'
    taste = {'algorithm_version': 1, 'feedback_count': count}
    for key in ('genre_affinity', 'language_affinity'):
        values = profile.get(key, {})
        if not isinstance(values, dict) or len(values) > 100:
            errors[f'taste_profile.{key}'] = 'Supply a feature map.'
            values = {}
        for feature, weight in values.items():
            valid_feature = feature in GENRES if key == 'genre_affinity' else bool(re.fullmatch('[a-z]{2}', feature))
            if not valid_feature or type(weight) not in {int, float} or not -1 <= weight <= 1 or (type(weight) is float and not math.isfinite(weight)):
                errors[f'taste_profile.{key}'] = 'Use supported features and finite weights from -1 to 1.'
        taste[key] = values
    identities = body.get('exclude', [])
    excluded = set()
    if not isinstance(identities, list):
        errors['exclude'] = 'Supply title identities.'
        identities = []
    for item in identities:
        if (not isinstance(item, dict) or set(item) != {'media_type', 'tmdb_id'}
                or not isinstance(item.get('media_type'), str) or item['media_type'] not in {'movie', 'tv'}
                or type(item.get('tmdb_id')) is not int or not 0 < item['tmdb_id'] <= 2147483647):
            errors['exclude'] = 'Use valid movie or tv identities.'
        else:
            excluded.add(f"{item['media_type']}:{item['tmdb_id']}")
    if len(excluded) > 1000:
        errors['exclude'] = 'At most 1000 distinct exclusions are supported; none were truncated.'
    if errors:
        raise ValidationError(errors)
    return {'filters': per_media, 'preferences': preferences, 'taste_profile': taste, 'exclude': excluded}
