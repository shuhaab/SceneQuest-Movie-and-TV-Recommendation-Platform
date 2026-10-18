"""Reliability, privacy, and optional demo limits."""
import logging
from copy import deepcopy
import unittest
from app import create_app
from app.rate_limit import RateLimiter
from app.services.catalogue import CatalogueService
from app.cache import TTLCache
from app.tmdb.client import TMDBClient
from app.tmdb.errors import TMDBError
from app.tmdb.control import RequestBudget
from app.tmdb.normalize import normalize_title
from test_catalogue import CatalogueTMDB
from tests_fixtures import FakeResponse, FakeSession


class ReliabilityTests(unittest.TestCase):
    def test_auth_errors_never_echo_upstream_body_or_credential(self):
        for status in (401, 403):
            session = FakeSession([FakeResponse(status, {'message': 'secret-credential'})])
            app = create_app(tmdb_client=TMDBClient(token='secret-credential', session=session))
            with app.test_client() as client:
                response = client.get('/api/v1/config')
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json['error']['code'], 'TOKEN_INVALID')
            self.assertNotIn('secret-credential', response.get_data(as_text=True))
            self.assertEqual(len(session.calls), 1)

    def test_retry_after_nonfinite_is_not_retried(self):
        for value in ('NaN', 'Infinity'):
            response = FakeResponse(429, {})
            response.headers['Retry-After'] = value
            session = FakeSession([response])
            with self.assertRaises(TMDBError):
                TMDBClient(token='secret', session=session).genres('movie')
            self.assertEqual(len(session.calls), 1)

    def test_invalid_provider_envelope_is_clean_error(self):
        tmdb = CatalogueTMDB()
        tmdb.availability = lambda *a, **kw: []
        service = CatalogueService(tmdb, TTLCache())
        with self.assertRaises(TMDBError) as error:
            service._availability('movie', 1, 'MY', RequestBudget(4))
        self.assertEqual(error.exception.code, 'UPSTREAM_RESPONSE_INVALID')
        app = create_app(tmdb_client=tmdb)
        result = app.test_client().get('/api/v1/titles/movie/1')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['availability']['status'], 'unavailable')
        self.assertEqual(result.headers['Cache-Control'], 'no-store')

    def test_malformed_page_and_extreme_numbers(self):
        from app.tmdb.genres import mapping_for_lists
        self.assertEqual(mapping_for_lists([{'id': []}, {'id': {}}], [{'id': True}]), [])
        for raw in ({'results': [], 'total_pages': True}, {'results': {}, 'total_pages': 1}):
            with self.assertRaises(TMDBError):
                CatalogueService._page(raw)
        title = normalize_title({'id': 1, 'runtime': 10**400, 'vote_count': 10**400}, 'movie')
        self.assertIsNone(title['runtime']['minutes'])
        self.assertIsNone(title['rating']['votes'])
        title = normalize_title({'id': 1, 'episode_run_time': [10**400, 30]}, 'tv')
        self.assertEqual(title['runtime']['minutes'], 30)
        with self.assertRaises(ValueError):
            normalize_title({'id': 2147483648}, 'movie')

    def test_rolling_limiter_expires_and_bounds_memory(self):
        now = [0]
        limiter = RateLimiter(max_clients=2, clock=lambda: now[0])
        self.assertIsNone(limiter.check('a', 'catalogue', 2))
        self.assertIsNone(limiter.check('a', 'catalogue', 2))
        self.assertEqual(limiter.check('a', 'catalogue', 2), 60)
        self.assertIsNone(limiter.check('a', 'recommendations', 1))
        self.assertIsNone(limiter.check('b', 'catalogue', 1))
        self.assertEqual(len(limiter._clients), 2)
        now[0] = 60
        self.assertIsNone(limiter.check('b', 'catalogue', 1))

    def test_limit_categories_health_and_untrusted_forwarded_headers(self):
        app = create_app(tmdb_client=CatalogueTMDB())
        app.config['ENABLE_RATE_LIMITS'] = True
        client = app.test_client()
        for i in range(60):
            response = client.get('/api/v1/search?media_type=movie&q=example', headers={'X-Forwarded-For': f'198.51.100.{i}'})
            self.assertEqual(response.status_code, 200)
        response = client.get('/api/v1/search?media_type=movie&q=example')
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn('Retry-After', response.headers)
        self.assertEqual(client.get('/health').status_code, 200)
        for _ in range(10):
            self.assertEqual(client.post('/api/v1/recommendations', json={}).status_code, 200)
        self.assertEqual(client.post('/api/v1/recommendations', json={}).status_code, 429)

    def test_local_limits_disabled_and_missing_token_recommendations(self):
        app = create_app(tmdb_client=TMDBClient(token='', session=FakeSession([])))
        app.config['ENABLE_RATE_LIMITS'] = False
        client = app.test_client()
        for _ in range(11):
            result = client.post('/api/v1/recommendations', json={})
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json['error']['code'], 'TOKEN_MISSING')
            self.assertEqual(result.headers['Cache-Control'], 'no-store')

    def test_logs_and_shared_cache_contain_no_personal_request(self):
        app = create_app(tmdb_client=CatalogueTMDB())
        personal = {'preferences': {'preferred_genres': ['drama']},
                    'taste_profile': {'feedback_count': 1, 'genre_affinity': {'drama': .333}},
                    'exclude': [{'media_type': 'movie', 'tmdb_id': 987654321}]}
        with self.assertLogs(app.logger, level=logging.INFO) as logs:
            result = app.test_client().post('/api/v1/recommendations', json=deepcopy(personal))
        output = '\n'.join(logs.output)
        self.assertIn(result.json['request_id'], output)
        for value in ('genre_affinity', '987654321', 'preferred_genres', 'Authorization'):
            self.assertNotIn(value, output)
        cached = repr(app.extensions['public_cache']._items)
        for value in ('987654321', 'genre_affinity', 'preferred_genres', 'shortlist'):
            self.assertNotIn(value, cached)
        self.assertEqual(result.headers['Cache-Control'], 'no-store')
        for body in ([], {'filters': {'unknown': 'private-value'}}):
            result = app.test_client().post('/api/v1/recommendations', json=body)
            self.assertEqual(result.headers['Cache-Control'], 'no-store')

    def test_browser_policy_limits_resources_and_referrers(self):
        response = create_app(tmdb_client=CatalogueTMDB()).test_client().get('/')
        self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        policy = response.headers['Content-Security-Policy']
        self.assertIn("script-src 'self'", policy)
        self.assertIn("img-src 'self' https://image.tmdb.org", policy)
        self.assertIn("frame-ancestors 'none'", policy)

    def test_attribution_asset_and_notice_render_locally(self):
        client = create_app(tmdb_client=CatalogueTMDB()).test_client()
        html = client.get('/').get_data(as_text=True)
        self.assertIn('/static/images/tmdb-logo.svg', html)
        self.assertIn('not endorsed or certified by TMDB', html)
        self.assertIn('JustWatch', html)
        self.assertIn('aggregated taste profile', html)
        self.assertIn('normal image requests', html)
        response = client.get('/static/images/tmdb-logo.svg')
        self.assertEqual(response.status_code, 200)
        response.close()
