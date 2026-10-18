"""Contract and eligibility tests using deterministic upstream fixtures."""

from copy import deepcopy
import unittest

from app import create_app
from app.services.catalogue import discovery_params
from app.schemas import catalogue_query
from app.tmdb.client import TMDBClient
from app.tmdb.control import RequestBudget
from app.tmdb.errors import TMDBError
from tests_fixtures import FakeResponse, FakeSession
from werkzeug.datastructures import MultiDict


def title(title_id, *, media="movie", year="2020-01-01", runtime=100):
    return {"id": title_id, "title" if media == "movie" else "name": f"Title {title_id}",
            "release_date" if media == "movie" else "first_air_date": year,
            "genre_ids": [18, 9648], "original_language": "en", "adult": False,
            "vote_average": 8, "vote_count": 100, "runtime": runtime,
            "episode_run_time": [22, 30, 44]}


class CatalogueTMDB:
    def __init__(self):
        self.calls = []
        self.items = [title(1), title(2)]
        self.fail_details = set()
        self.fail_availability = False
        self.regional = {"MY": {"flatrate": [{"provider_id": 8, "provider_name": "Netflix"}],
                                "rent": [{"provider_id": 9, "provider_name": "Rental"}],
                                "link": "https://www.themoviedb.org/movie/1/watch?locale=MY"},
                         "US": {"flatrate": [{"provider_id": 9, "provider_name": "Other"}]}}

    def search(self, media, params, *, budget):
        self.calls.append(("search", media, params))
        return {"results": deepcopy(self.items), "total_pages": 2, "total_results": 40}

    def discover(self, media, params, *, budget):
        self.calls.append(("discover", media, params))
        return {"results": deepcopy(self.items), "total_pages": 2, "total_results": 40}

    def details(self, media, title_id, language, *, budget):
        self.calls.append(("details", media, title_id))
        if title_id in self.fail_details:
            raise TMDBError("UPSTREAM_UNAVAILABLE", "TMDB is temporarily unavailable.")
        item = next(item for item in self.items if item["id"] == title_id)
        return deepcopy(item)

    def availability(self, media, title_id, *, budget):
        self.calls.append(("availability", media, title_id))
        if self.fail_availability:
            raise TMDBError("UPSTREAM_UNAVAILABLE", "TMDB is temporarily unavailable.")
        return {"results": deepcopy(self.regional)}

    def providers(self, media, region, language, *, budget):
        self.calls.append(("providers", media, region))
        return {"results": self.regional.get(region, {}).get("flatrate", [])}


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.tmdb = CatalogueTMDB()
        self.app = create_app(tmdb_client=self.tmdb)
        self.client = self.app.test_client()

    def test_search_pagination_and_filter_separation(self):
        response = self.client.get("/api/v1/search?media_type=movie&q=arrival&page=2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["page"], 2)
        self.assertEqual(response.json["total_results"], 40)
        self.assertEqual(self.tmdb.calls[0][2]["include_adult"], "false")
        for query in ("media_type=both&q=arrival", "media_type=movie&q=arrival&rating_min=7",
                      "media_type=movie&q=arrival&page=0", "media_type=movie&q=arrival&page=1&page=2"):
            self.assertEqual(self.client.get("/api/v1/search?" + query).status_code, 422)

    def test_discovery_translation_uses_or_and_inclusive_years(self):
        filters = catalogue_query(MultiDict({"media_type": "movie", "genre_keys": "drama,mystery",
                                             "provider_ids": "8,9", "year_from": "2015", "year_to": "2020",
                                             "runtime_max": "150"}), "discover")
        params = discovery_params(filters)
        self.assertEqual(params["with_genres"], "18|9648")
        self.assertEqual(params["with_watch_providers"], "8|9")
        self.assertEqual(params["watch_region"], "MY")
        self.assertEqual(params["primary_release_date.gte"], "2015-01-01")
        self.assertEqual(params["primary_release_date.lte"], "2020-12-31")
        filters["media_type"] = "tv"
        tv_params = discovery_params(filters)
        self.assertEqual(tv_params["first_air_date.lte"], "2020-12-31")
        self.assertNotIn("with_runtime.lte", tv_params)

    def test_invalid_filter_values_never_reach_upstream(self):
        for query in ("media_type=tv&genre_keys=horror", "media_type=movie&year_from=2020&year_to=2015",
                      "media_type=movie&rating_min=nan", "media_type=movie&runtime_max=0",
                      "media_type=movie&region=XX", "media_type=movie&provider_ids=0",
                      "media_type=movie&access_type=subscription"):
            response = self.client.get("/api/v1/discover?" + query)
            self.assertEqual(response.status_code, 422, query)
        self.assertEqual(self.tmdb.calls, [])

    def test_unknown_runtime_and_year_fail_active_constraints(self):
        self.tmdb.items = [title(1, year="2015-01-01"), title(2, year="2020-12-31"),
                           title(3, year=""), title(4, runtime=None), title(5, year="2021-01-01")]
        response = self.client.get("/api/v1/discover?media_type=movie&year_from=2015&year_to=2020&runtime_max=120")
        self.assertEqual([item["tmdb_id"] for item in response.json["results"]], [1, 2])
        self.assertTrue(response.json["verification_complete"])
        self.assertNotIn("total_results", response.json)

    def test_tv_runtime_is_verified_from_median(self):
        self.tmdb.items = [title(1, media="tv"), title(2, media="tv"), title(3, media="tv")]
        self.tmdb.items[1]["episode_run_time"] = []
        self.tmdb.items[2]["episode_run_time"] = [45, 60, 90]
        response = self.client.get("/api/v1/discover?media_type=tv&runtime_max=30")
        self.assertEqual([item["key"] for item in response.json["results"]], ["tv:1"])
        self.assertEqual(response.json["results"][0]["runtime"]["minutes"], 30)

    def test_provider_verification_respects_region_and_access_type(self):
        my = self.client.get("/api/v1/discover?media_type=movie&provider_ids=8&region=MY")
        us = self.client.get("/api/v1/discover?media_type=movie&provider_ids=8&region=US")
        rental = self.client.get("/api/v1/discover?media_type=movie&provider_ids=8&region=MY&access_type=rent")
        self.assertEqual(len(my.json["results"]), 2)
        self.assertEqual(us.json["results"], [])
        self.assertEqual(rental.json["results"], [])
        self.assertIn("checked_at", my.json["results"][0]["availability"])

    def test_partial_verification_omits_failed_title(self):
        self.tmdb.fail_details = {2}
        response = self.client.get("/api/v1/discover?media_type=movie&runtime_max=120")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["tmdb_id"] for item in response.json["results"]], [1])
        self.assertFalse(response.json["verification_complete"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_stale_availability_is_displayed_but_cannot_pass_discovery(self):
        cache = self.app.extensions["public_cache"]
        cache.set(("availability", "movie", 1, "MY"),
                  ({"results": self.tmdb.regional}, "2020-01-01T00:00:00Z"), -1)
        self.tmdb.fail_availability = True
        details = self.client.get("/api/v1/titles/movie/1")
        self.assertEqual(details.status_code, 200)
        self.assertTrue(details.json["availability"]["stale"])
        self.assertEqual(details.json["availability"]["checked_at"], "2020-01-01T00:00:00Z")
        self.assertEqual(details.headers["Cache-Control"], "no-store")
        discovery = self.client.get("/api/v1/discover?media_type=movie&provider_ids=8")
        self.assertEqual(discovery.json["results"], [])
        self.assertFalse(discovery.json["verification_complete"])

    def test_title_cache_uses_composite_identity(self):
        self.client.get("/api/v1/titles/movie/1")
        self.tmdb.items = [title(1, media="tv")]
        response = self.client.get("/api/v1/titles/tv/1")
        self.assertEqual(response.json["key"], "tv:1")
        self.assertIn(("details", "movie", 1), self.tmdb.calls)
        self.assertIn(("details", "tv", 1), self.tmdb.calls)

    def test_provider_choices_and_structured_not_found(self):
        response = self.client.get("/api/v1/providers?media_type=tv&region=US")
        self.assertEqual(response.json["providers"][0]["id"], 9)
        unknown = self.client.get("/api/v1/no-such-route")
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unknown.json["error"]["code"], "NOT_FOUND")

    def test_http_retry_counts_against_attempt_budget(self):
        session = FakeSession([FakeResponse(503, {})])
        client = TMDBClient(token="token", session=session)
        with self.assertRaises(TMDBError) as caught:
            client.genres("movie", budget=RequestBudget(1))
        self.assertEqual(caught.exception.code, "WORK_LIMIT_REACHED")
        self.assertEqual(len(session.calls), 1)

    def test_expired_budget_prevents_upstream_call(self):
        session = FakeSession([])
        client = TMDBClient(token="token", session=session)
        with self.assertRaises(TMDBError):
            client.genres("movie", budget=RequestBudget(8, seconds=0))
        self.assertEqual(session.calls, [])

    def test_batch_refresh_returns_successes_and_per_item_failures(self):
        self.tmdb.fail_details = {2}
        response = self.client.post('/api/v1/titles/batch', json={"titles": [
            {"media_type": "movie", "tmdb_id": 1}, {"media_type": "movie", "tmdb_id": 2},
            {"media_type": "movie", "tmdb_id": 1}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([t['key'] for t in response.json['titles']], ['movie:1'])
        self.assertEqual(response.json['errors'][0]['key'], 'movie:2')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_batch_limits_malformed_json_and_boolean_ids(self):
        oversized = [{"media_type": "movie", "tmdb_id": i} for i in range(1, 22)]
        for body in ({"titles": oversized}, {"titles": [{"media_type": "movie", "tmdb_id": True}]}, {"titles": [{"media_type": [], "tmdb_id": 1}]}, {"titles": []}):
            self.assertEqual(self.client.post('/api/v1/titles/batch', json=body).status_code, 422)
        self.assertEqual(self.client.post('/api/v1/titles/batch', data='{broken', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post('/api/v1/titles/batch', data='x' * 65537, content_type='application/json').status_code, 413)

    def test_ui_is_served_without_upstream_credentials(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'SceneQuest', response.data)
        self.assertIn(b'js/ui.js', response.data)
        self.assertEqual(self.tmdb.calls, [])


if __name__ == "__main__":
    unittest.main()
