import unittest
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import create_app
from app.tmdb.genres import keys_for_ids, mapping_for_lists
from app.tmdb.normalize import normalize_title
from app.tmdb.client import TMDBClient


class FakeTMDB:
    def __init__(self):
        self.calls = []

    def genres(self, media_type, language, *, budget=None):
        self.calls.append((media_type, language))
        ids = [18, 9648, 28] if media_type == "movie" else [18, 9648, 10759]
        return [{"id": value, "name": str(value)} for value in ids]


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body
        self.headers = {}
        self.ok = 200 <= status_code < 300

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


class FoundationTests(unittest.TestCase):
    def test_composite_identity_and_media_specific_genres(self):
        movie = normalize_title({"id": 42, "title": "Film", "genre_ids": [28, 18]},
                                "movie", checked_at="2026-10-06T00:00:00Z")
        tv = normalize_title({"id": 42, "name": "Show", "genre_ids": [10759, 18]},
                             "tv", checked_at="2026-10-06T00:00:00Z")
        self.assertEqual(movie["key"], "movie:42")
        self.assertEqual(tv["key"], "tv:42")
        self.assertEqual(movie["genre_keys"], ["action", "drama"])
        self.assertEqual(tv["genre_keys"], ["action_adventure", "drama"])
        self.assertEqual(keys_for_ids("tv", [None]), [])

    def test_tv_runtime_uses_median_of_valid_positive_values(self):
        title = normalize_title({"id": 1, "name": "Show", "episode_run_time": [0, 22, 44, 30, -1, True]},
                                "tv")
        self.assertEqual(title["runtime"], {"minutes": 30, "basis": "tv_episode"})
        unknown = normalize_title({"id": 2, "name": "Unknown", "episode_run_time": []}, "tv")
        self.assertIsNone(unknown["runtime"]["minutes"])

    def test_genre_mapping_does_not_claim_false_counterpart(self):
        mapped = mapping_for_lists([{"id": 28}, {"id": 18}], [{"id": 10759}, {"id": 18}])
        by_key = {item["key"]: item["tmdb_ids"] for item in mapped}
        self.assertEqual(by_key["action"], {"movie": 28})
        self.assertEqual(by_key["action_adventure"], {"tv": 10759})
        self.assertEqual(by_key["drama"], {"movie": 18, "tv": 18})

    def test_config_caches_public_mapping_and_rejects_unsupported_language(self):
        tmdb = FakeTMDB()
        app = create_app(tmdb_client=tmdb)
        with app.test_client() as client:
            first = client.get("/api/v1/config")
            second = client.get("/api/v1/config")
            invalid = client.get("/api/v1/config?display_language=fr-FR")
            health = client.get("/health")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(tmdb.calls, [("movie", "en-US"), ("tv", "en-US")])
        self.assertEqual(first.json["defaults"]["region"], "MY")
        self.assertIn("X-Request-ID", first.headers)
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(health.json, {"status": "ok"})

    def test_client_retries_transient_error_without_exposing_token_in_url(self):
        session = FakeSession([FakeResponse(503, {}),
                               FakeResponse(200, {"genres": [{"id": 18, "name": "Drama"}]})])
        client = TMDBClient(token="secret-token", session=session)
        self.assertEqual(client.genres("movie"), [{"id": 18, "name": "Drama"}])
        self.assertEqual(len(session.calls), 2)
        url, options = session.calls[0]
        self.assertEqual(url, "https://api.themoviedb.org/3/genre/movie/list")
        self.assertNotIn("secret-token", url)
        self.assertEqual(options["headers"]["Authorization"], "Bearer secret-token")

    def test_saved_token_loads_without_a_shell_export(self):
        with TemporaryDirectory() as directory:
            env_file = Path(directory) / '.env'
            env_file.write_text('TMDB_ACCESS_TOKEN=fixture-file-token\n')
            with patch('app.ENV_FILE', env_file), patch.dict(os.environ):
                os.environ.pop('TMDB_ACCESS_TOKEN', None)
                app = create_app()
                self.assertEqual(app.extensions['tmdb_client']._token, 'fixture-file-token')
                html = app.test_client().get('/').get_data(as_text=True)
                self.assertNotIn('fixture-file-token', html)

    def test_shell_token_takes_priority_over_saved_file(self):
        with TemporaryDirectory() as directory:
            env_file = Path(directory) / '.env'
            env_file.write_text('TMDB_ACCESS_TOKEN=fixture-file-token\n')
            with patch('app.ENV_FILE', env_file), patch.dict(os.environ, {'TMDB_ACCESS_TOKEN': 'fixture-shell-token'}):
                self.assertEqual(create_app().extensions['tmdb_client']._token, 'fixture-shell-token')

    def test_missing_token_is_a_clean_service_error(self):
        app = create_app(tmdb_client=TMDBClient(token="", session=FakeSession([])))
        with app.test_client() as client:
            response = client.get("/api/v1/config")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["error"]["code"], "TOKEN_MISSING")
        self.assertNotIn("secret", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
