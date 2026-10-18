"""Bounded candidate retrieval, verified ranking, and deterministic diversity."""
from app.services.catalogue import CatalogueService, discovery_params, matches_basic, matches_runtime
from app.tmdb.control import RequestBudget
from app.tmdb.errors import TMDBError
from app.tmdb.genres import GENRES


def weighted(parts):
    return sum(value * weight for value, weight in parts) / sum(weight for _, weight in parts)


def score(title, preferences, profile):
    genres, language = set(title['genre_keys']), title['original_language']
    explicit, inferred = [], []
    preferred = set(preferences['preferred_genres'])
    if preferred:
        explicit.append((len(genres & preferred) / len(preferred), .8))
    if preferences['preferred_original_languages']:
        explicit.append((float(language in preferences['preferred_original_languages']), .2))
    if profile['feedback_count'] > 0:
        affinity = profile['genre_affinity']
        if affinity:
            mean = sum(affinity.get(g, 0) for g in genres) / len(genres) if genres else 0
            inferred.append(((mean + 1) / 2, .8))
        if profile['language_affinity']:
            inferred.append(((profile['language_affinity'].get(language, 0) + 1) / 2, .2))
    average, votes = title['rating']['average'], title['rating']['votes']
    confidence = ((votes * average + 600) / (votes + 100)) / 10 if average is not None and votes else .6
    parts = [(min(1, max(0, confidence)), .2)]
    if explicit:
        parts.append((weighted(explicit), .45))
    if inferred:
        parts.append((weighted(inferred), .35))
    return weighted(parts)


def diverse(ranked, both):
    selected = []
    remaining = list(ranked)
    while remaining and len(selected) < 3:
        def adjusted(item):
            title, value = item
            genre_set = set(title['genre_keys'])
            overlap = max((len(genre_set & set(t['genre_keys'])) / len(genre_set | set(t['genre_keys']))
                           if genre_set | set(t['genre_keys']) else 0 for t in selected), default=0)
            bonus = .03 if selected and both and title['media_type'] not in {t['media_type'] for t in selected} else 0
            return value - .05 * overlap + bonus
        chosen = min(remaining, key=lambda item: (-adjusted(item), item[0]['key']))
        selected.append(chosen[0])
        remaining.remove(chosen)
    return selected


def reasons(title, preferences, profile, filters):
    result = []
    matched = set(title['genre_keys']) & set(preferences['preferred_genres'])
    if matched:
        result.append('Matches preferred genres: ' + ', '.join(GENRES[g][0] for g in sorted(matched)) + '.')
    if title['original_language'] in preferences['preferred_original_languages']:
        result.append(f"Matches your preferred original language ({title['original_language']}).")
    liked = [g for g in title['genre_keys'] if profile['feedback_count'] > 0 and profile['genre_affinity'].get(g, 0) > 0]
    if liked:
        result.append('Your feedback favours ' + ', '.join(GENRES[g][0] for g in sorted(liked)) + '.')
    if profile['feedback_count'] > 0 and profile['language_affinity'].get(title['original_language'], 0) > 0:
        result.append(f"Your feedback favours titles in {title['original_language']}.")
    if filters['provider_ids']:
        providers = title['availability']['providers'][filters['access_type']]
        names = sorted({p['name'] for p in providers if p['id'] in filters['provider_ids']})
        result.append(f"Available through {', '.join(names)} in {filters['region']} ({filters['access_type']}).")
    if not result:
        rating = title['rating']
        if rating['average'] is not None and rating['votes']:
            result.append(f"Rated {rating['average']:g}/10 from {rating['votes']:,} votes; eligible for your search.")
        else:
            result.append('Eligible for your selected search; rating confidence uses a neutral prior.')
    return result


class RecommendationService(CatalogueService):
    def recommend(self, request):
        budget = RequestBudget(28, seconds=8)
        preferences, profile = request['preferences'], request['taste_profile']
        candidates, warnings, upstream_codes = {}, set(), set()
        page_successes, limit_reached = 0, False
        for media, filters in request['filters'].items():
            taste = [g for g in preferences['preferred_genres'] if media in GENRES[g][1]]
            if not preferences['preferred_genres'] and profile['feedback_count'] > 0:
                taste = sorted((g for g, value in profile['genre_affinity'].items() if value > 0 and media in GENRES[g][1]), key=lambda g: (-profile['genre_affinity'][g], g))
            if filters['genre_keys']:
                taste = [g for g in taste if g in filters['genre_keys']]
            taste = taste[:2]
            plans = [(1, taste), (1, filters['genre_keys'])] if taste else [(1, filters['genre_keys']), (2, filters['genre_keys'])]
            for page, category in plans:
                query = dict(filters, page=page, genre_keys=category)
                params = discovery_params(query)
                try:
                    raw, checked_at, _ = self._fetch(('discover', media, tuple(sorted(params.items()))), lambda: self.tmdb.discover(media, params, budget=budget), 600)
                    items = self._page(raw)
                    page_successes += 1
                    limit_reached |= raw['total_pages'] > page if page == 2 or taste else raw['total_pages'] > 2
                    for item in items:
                        if isinstance(item, dict) and item.get('adult') is True:
                            continue
                        try:
                            title = self._normalize(item, media, checked_at)
                        except TMDBError as error:
                            upstream_codes.add(error.code)
                            warnings.add('An invalid catalogue title was omitted.')
                            continue
                        if title['key'] not in request['exclude'] and matches_basic(title, filters):
                            candidates[title['key']] = title
                    if not taste and raw['total_pages'] < 2:
                        break
                except TMDBError as error:
                    upstream_codes.add(error.code)
                    warnings.add('Some catalogue results could not be retrieved or validated.')
                    if error.code == 'WORK_LIMIT_REACHED':
                        break
        ordered = sorted(candidates.values(), key=lambda t: (-score(t, preferences, profile), t['key']))
        verified = []
        examined = 0
        for candidate in ordered[:12]:
            examined += 1
            media = candidate['media_type']
            filters = request['filters'][media]
            try:
                title = self._detail(media, candidate['tmdb_id'], filters['display_language'], budget)
                if not matches_basic(title, filters) or not matches_runtime(title, filters):
                    continue
                try:
                    title['availability'] = self._availability(media, title['tmdb_id'], filters['region'], budget)
                except TMDBError as error:
                    upstream_codes.add(error.code)
                    warnings.add('Regional availability could not be verified for some titles.')
                    if filters['provider_ids']:
                        continue
                    title['availability'] = {'region': filters['region'], 'status': 'unavailable', 'checked_at': None}
                if filters['provider_ids']:
                    ids = {p['id'] for p in title['availability']['providers'][filters['access_type']]}
                    if not ids & set(filters['provider_ids']):
                        continue
                title['reasons'] = reasons(title, preferences, profile, filters)
                verified.append((title, score(title, preferences, profile)))
                if len(verified) == 6:
                    break
            except TMDBError as error:
                upstream_codes.add(error.code)
                warnings.add('Some titles could not be verified and were omitted.')
                if error.code == 'WORK_LIMIT_REACHED':
                    break
        limit_reached |= examined < len(ordered)
        shortlist = diverse(verified, len(request['filters']) == 2)
        retrieval = ('partial' if page_successes else 'failed') if warnings else 'completed'
        result = {'shortlist': shortlist, 'result_status': 'full' if len(shortlist) == 3 else 'limited' if shortlist else 'empty',
                  'retrieval_status': retrieval, 'search_complete': retrieval == 'completed',
                  'candidate_limit_reached': limit_reached, 'warnings': sorted(warnings)}
        status = 503 if not shortlist and retrieval != 'completed' else 200
        if status == 503:
            code = 'TOKEN_INVALID' if 'TOKEN_INVALID' in upstream_codes else 'TOKEN_MISSING' if 'TOKEN_MISSING' in upstream_codes else 'UPSTREAM_UNAVAILABLE'
            messages = {'TOKEN_INVALID': 'TMDB rejected the server credential. Check the API Read Access Token and restart Flask.',
                        'TOKEN_MISSING': 'Catalogue access is not configured. Set the server TMDB access token and restart Flask.',
                        'UPSTREAM_UNAVAILABLE': 'Recommendations could not be verified. Try again later.'}
            result['error'] = {'code': code, 'message': messages[code], 'fields': {}}
        return result, status
