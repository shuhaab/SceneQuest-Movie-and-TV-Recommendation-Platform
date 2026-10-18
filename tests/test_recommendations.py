"""Recommendation contracts and ranking with a bounded deterministic catalogue."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from app import create_app
from app.recommendation_schema import recommendation_request
from app.services.recommendations import score, diverse
from app.tmdb.normalize import normalize_title
from app.tmdb.errors import TMDBError
from test_catalogue import CatalogueTMDB, title


class RecommendationTMDB(CatalogueTMDB):
    def __init__(self):
        super().__init__()
        self.items = [title(i) for i in range(1, 16)]
        self.total_pages = 3
        self.fail_media = set()
        self.detail_overrides = {}
        self.budgets = []

    def use(self, budget):
        budget.consume()
        self.budgets.append(budget)

    def discover(self, media, params, *, budget):
        self.use(budget)
        self.calls.append(('discover', media, deepcopy(params)))
        if media in self.fail_media:
            raise TMDBError('UPSTREAM_UNAVAILABLE', 'Unavailable')
        return {'results': deepcopy(self.items), 'total_pages': self.total_pages}

    def details(self, media, title_id, language, *, budget):
        self.use(budget)
        value = super().details(media, title_id, language, budget=budget)
        value.update(self.detail_overrides.get(title_id, {}))
        return value

    def availability(self, media, title_id, *, budget):
        self.use(budget)
        return super().availability(media, title_id, budget=budget)


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.tmdb = RecommendationTMDB()
        self.client = create_app(tmdb_client=self.tmdb).test_client()

    def request(self, **body):
        return self.client.post('/api/v1/recommendations', json=body)

    def test_full_shortlist_shared_budget_and_cache(self):
        result = self.request()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers['Cache-Control'], 'no-store')
        self.assertEqual(result.json['result_status'], 'full')
        self.assertEqual(result.json['retrieval_status'], 'completed')
        self.assertTrue(result.json['candidate_limit_reached'])
        self.assertEqual(len([c for c in self.tmdb.calls if c[0] == 'details']), 6)
        self.assertEqual(len({id(b) for b in self.tmdb.budgets}), 1)
        self.assertLessEqual(self.tmdb.budgets[0].attempts, 28)
        self.assertTrue(all('score' not in t and t['reasons'] for t in result.json['shortlist']))
        calls = len(self.tmdb.calls)
        self.assertEqual(self.request().status_code, 200)
        self.assertEqual(len(self.tmdb.calls), calls)

    def test_invalid_candidate_does_not_discard_later_valid_titles(self):
        self.tmdb.items = [{'id': None}, *[title(i) for i in range(1, 5)]]
        self.tmdb.total_pages = 1
        result = self.request(filters={'media_type': 'movie'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['result_status'], 'full')
        self.assertEqual(result.json['retrieval_status'], 'partial')
        self.assertEqual(len(result.json['shortlist']), 3)
        self.assertTrue(any('invalid catalogue title' in value for value in result.json['warnings']))

    def test_exclusions_use_composite_id_and_deduplicate(self):
        self.tmdb.items = [title(1)]
        self.tmdb.total_pages = 1
        result = self.request(exclude=[{'media_type': 'movie', 'tmdb_id': 1}])
        self.assertEqual([t['key'] for t in result.json['shortlist']], ['tv:1'])
        self.assertEqual(result.json['result_status'], 'limited')

    def test_empty_completed_and_failed_are_distinct(self):
        self.tmdb.items = []
        result = self.request()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['retrieval_status'], 'completed')
        self.assertTrue(result.json['search_complete'])
        self.tmdb.fail_media = {'movie', 'tv'}
        # A fresh application prevents public cache hiding the outage.
        self.client = create_app(tmdb_client=self.tmdb).test_client()
        result = self.request()
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json['retrieval_status'], 'failed')
        self.assertFalse(result.json['search_complete'])

    def test_partial_can_have_full_results(self):
        self.tmdb.fail_media = {'tv'}
        result = self.request()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['result_status'], 'full')
        self.assertEqual(result.json['retrieval_status'], 'partial')

    def test_availability_failure_retained_only_without_provider_constraint(self):
        self.tmdb.fail_availability = True
        result = self.request(filters={'media_type': 'movie'})
        self.assertEqual(result.json['result_status'], 'full')
        self.assertEqual(result.json['retrieval_status'], 'partial')
        self.assertEqual(result.json['shortlist'][0]['availability']['status'], 'unavailable')
        result = self.request(filters={'media_type': 'movie', 'provider_ids': [8]})
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json['shortlist'], [])

    def test_provider_country_access_and_unknown_runtime_are_strict(self):
        for filters in ({'region': 'US', 'provider_ids': [8]}, {'provider_ids': [8], 'access_type': 'rent'},
                        {'runtime_max': {'movie': 90}}):
            result = self.request(filters={'media_type': 'movie', **filters})
            self.assertEqual(result.json['shortlist'], [], filters)
            self.assertEqual(result.json['retrieval_status'], 'completed')
        self.tmdb.detail_overrides = {i: {'runtime': None} for i in range(1, 16)}
        self.client = create_app(tmdb_client=self.tmdb).test_client()
        self.assertEqual(self.request(filters={'media_type': 'movie', 'runtime_max': {'movie': 150}}).json['shortlist'], [])
        result = self.request(filters={'media_type': 'movie', 'provider_ids': [8]})
        self.assertTrue(any('Netflix' in reason for reason in result.json['shortlist'][0]['reasons']))

    def test_details_recheck_year_and_rerank_features(self):
        self.tmdb.items = [title(i) for i in range(1, 5)]
        self.tmdb.detail_overrides = {1: {'release_date': '1990-01-01'}, 2: {'genre_ids': [35]}, 3: {'genre_ids': [18]}}
        result = self.request(filters={'media_type': 'movie', 'year_from': 2000}, preferences={'preferred_genres': ['comedy']})
        self.assertNotIn('movie:1', [t['key'] for t in result.json['shortlist']])
        self.assertEqual(result.json['shortlist'][0]['key'], 'movie:2')

    def test_taste_and_broad_queries_keep_hard_filters(self):
        self.request(filters={'media_type': 'movie', 'genre_keys': ['drama', 'mystery'], 'year_from': 2000},
                     preferences={'preferred_genres': ['drama', 'comedy']})
        queries = [c[2] for c in self.tmdb.calls if c[0] == 'discover']
        self.assertEqual([q['with_genres'] for q in queries], ['18', '18|9648'])
        self.assertTrue(all(q['primary_release_date.gte'] == '2000-01-01' for q in queries))

    def test_inferred_candidate_genres_and_no_compatible_taste(self):
        self.request(filters={'media_type': 'tv'}, taste_profile={'feedback_count': 2, 'genre_affinity': {'comedy': .5, 'drama': .25, 'mystery': -.5}})
        queries = [c[2] for c in self.tmdb.calls if c[0] == 'discover']
        self.assertEqual(queries[0]['with_genres'], '35|18')
        self.assertNotIn('with_genres', queries[1])
        self.tmdb.calls.clear()
        self.request(filters={'media_type': 'movie'}, preferences={'preferred_genres': ['news']})
        self.assertEqual([c[2]['page'] for c in self.tmdb.calls if c[0] == 'discover'], [1, 2])

    def test_verification_ceiling_does_not_relax_filters(self):
        result = self.request(filters={'media_type': 'movie', 'runtime_max': {'movie': 90}})
        self.assertEqual(len([c for c in self.tmdb.calls if c[0] == 'details']), 12)
        self.assertTrue(result.json['candidate_limit_reached'])
        self.assertTrue(result.json['search_complete'])

    def test_all_detail_failures_partial_503(self):
        self.tmdb.fail_details = set(range(1, 16))
        result = self.request(filters={'media_type': 'movie'})
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json['retrieval_status'], 'partial')

    def test_expired_availability_cannot_pass_provider_constraint(self):
        app = create_app(tmdb_client=self.tmdb)
        self.client = app.test_client()
        self.tmdb.items = [title(1)]
        self.tmdb.total_pages = 1
        self.assertEqual(self.request(filters={'media_type': 'movie', 'provider_ids': [8]}).status_code, 200)
        cache = app.extensions['public_cache']
        key = ('availability', 'movie', 1, 'MY')
        cache.set(key, cache.lookup(key)[0], -1)
        self.tmdb.fail_availability = True
        result = self.request(filters={'media_type': 'movie', 'provider_ids': [8]})
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json['shortlist'], [])

    def test_retry_attempts_consume_shared_ceiling(self):
        original = self.tmdb.use
        def retried(budget):
            original(budget)
            original(budget)
        self.tmdb.use = retried
        self.tmdb.fail_availability = True
        result = self.request(filters={'provider_ids': [8]})
        self.assertEqual(result.status_code, 503)
        self.assertEqual(self.tmdb.budgets[0].attempts, 28)
        self.assertEqual(len({id(b) for b in self.tmdb.budgets}), 1)

    def test_bad_inputs_and_body_limit(self):
        bodies = [{'filters': {'media_type': 'both', 'genre_keys': ['horror']}},
                  {'filters': {'runtime_max': 90}}, {'filters': {'year_from': True}},
                  {'filters': {'rating_min': float('nan')}}, {'filters': {'rating_min': 10**400}}, {'preferences': []},
                  {'taste_profile': {'algorithm_version': 2}}, {'taste_profile': {'genre_affinity': {'drama': 2}}},
                  {'exclude': [{'media_type': 'movie', 'tmdb_id': True}]},
                  {'exclude': [{'media_type': 'movie', 'tmdb_id': i} for i in range(1, 1002)]}]
        for body in bodies:
            self.assertEqual(self.request(**body).status_code, 422, body)
        result = self.client.post('/api/v1/recommendations', data='x' * 65537, content_type='application/json')
        self.assertEqual(result.status_code, 413)
        self.assertEqual(self.tmdb.calls, [])

    def test_deadline_produces_failed_retrieval(self):
        from app.tmdb.control import RequestBudget
        with patch('app.services.recommendations.RequestBudget', side_effect=lambda *a, **kw: RequestBudget(28, seconds=0)):
            result = self.request()
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json['retrieval_status'], 'failed')

    def test_score_weight_redistribution_prior_and_negative_affinity(self):
        normalized = normalize_title(title(1), 'movie', checked_at='now')
        request = recommendation_request({})
        p, t = request['preferences'], request['taste_profile']
        self.assertAlmostEqual(score(normalized, p, t), .7)
        p['preferred_genres'] = ['drama']
        self.assertAlmostEqual(score(normalized, p, t), (.45 + .2 * .7) / .65)
        t.update(feedback_count=1, genre_affinity={'drama': -.5, 'mystery': -.5})
        self.assertAlmostEqual(score(normalized, p, t), .45 + .35 * .25 + .2 * .7)
        normalized['rating'] = {'average': None, 'votes': None}
        self.assertAlmostEqual(score(normalized, {'preferred_genres': [], 'preferred_original_languages': []}, dict(t, feedback_count=0)), .6)

    def test_diversity_and_ties_are_deterministic(self):
        titles = [normalize_title(title(i), 'movie', checked_at='now') for i in [3, 2, 1]]
        tv = normalize_title(title(1, media='tv'), 'tv', checked_at='now')
        result = diverse([(t, .7) for t in [*titles, tv]], True)
        self.assertEqual([t['key'] for t in result], ['movie:1', 'tv:1', 'movie:2'])


if __name__ == '__main__':
    unittest.main()
