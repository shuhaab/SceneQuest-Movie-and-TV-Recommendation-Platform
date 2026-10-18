# SceneQuest

Personal movie and TV discovery app, under construction. See [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) for the planned behavior and delivery order.

## Run locally

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp -n .env.example .env
# Edit .env and set TMDB_ACCESS_TOKEN to your API Read Access Token.
.venv/bin/flask --app run run --port 5002
```

Open [SceneQuest](http://127.0.0.1:5002/) for the browser interface, or [the health endpoint](http://127.0.0.1:5002/health) to check the local application. The project-root `.env` file is loaded automatically on startup, including with `python run.py`. Enter the token once in that file; it is excluded from Git. Existing shell environment variables take priority over `.env`. If an old exported value interferes, run `unset TMDB_ACCESS_TOKEN` before restarting Flask. Credentials are sent to TMDB only by the server. The current API supports English display text and the MY, US, and GB regions.

Browse Movies or TV, search within a tab, or apply discovery filters. Open a title for details and regional availability. Preferences, watchlist entries, history, and dismissed titles are saved under `scenequest.state` in this browser. Marking watched records neutral feedback; likes and dislikes are explicit actions. Preferred genres stay separate from hard discovery filters.

Open **For you** and select **Generate recommendations** to use preferred genres, preferred languages, and smoothed explicit likes/dislikes. Watched and dismissed titles are always excluded; watchlisted titles are excluded by default, with an include option. Neutral watched entries do not influence taste. Recommendation filters are strict and never automatically relaxed.

Saved lists render from compact metadata snapshots and refresh visible titles in batches of up to 20. Catalogue outages do not prevent list edits. Invalid or incompatible browser data is preserved until you choose **Clear personal data**; storage failures display a warning and retain edits in memory. Clearing data requires confirmation in the interface. Export/import, migrations, and synchronization across tabs are deferred.

## Implemented API

| Endpoint | Behavior |
|---|---|
| `GET /health` | Local health check without TMDB calls. |
| `GET /api/v1/config` | Defaults, limits, and explicit movie/TV genre mappings fetched from TMDB. |
| `GET /api/v1/search?media_type=movie&q=arrival&page=1` | Title search with upstream pagination; choose `movie` or `tv`. |
| `GET /api/v1/discover?media_type=tv&genre_keys=drama,mystery&runtime_max=45` | Discovery with genre, provider, rating, inclusive year, original language, and runtime filters. |
| `GET /api/v1/titles/movie/123?region=MY` | Normalized details with regional providers, access modes, checked timestamps, and a TMDB watch link when supplied. |
| `GET /api/v1/providers?media_type=movie&region=MY` | Provider choices for the selected media type and country. |
| `POST /api/v1/recommendations` | Up to three verified, personalized titles with reasons and retrieval status; never publicly cached. |
| `POST /api/v1/titles/batch` | Refresh up to 20 title identities; returns `titles` and per-item `errors`. |

Discovery accepts `genre_keys` and `provider_ids` as comma-separated lists. Each list means match **any** selected value. Supported access types are `flatrate` (default), `free`, `ads`, `rent`, and `buy`. Bounds use `rating_min/max` (0–10), `year_from/to` (1870–2100), and `runtime_min/max` (1–1000 minutes). `original_language` uses a two-letter code. Search accepts only title-search parameters; advanced filters belong to discovery.

Discovery pages refer to upstream source pages. Results may contain fewer than 20 verified titles. `verification_complete=false` and warnings identify unfinished work; upstream totals are not advertised as exact eligible totals. TV runtime uses the median of positive episode runtimes. Unknown dates or runtimes fail an active constraint.

Public metadata uses a bounded in-memory cache. Details may display labelled stale data during upstream outages. Discovery runtime and provider verification requires fresh cache data. Upstream requests share an 8-second work budget, a per-route attempt ceiling including retries, and a process-wide concurrency ceiling of four. The deadline controls scheduled work; HTTP timeouts do not guarantee a strict end-to-end response duration.

Recommendation JSON sections are `filters`, `preferences`, `taste_profile` (algorithm version 1), and `exclude` (movie/TV identities, at most 1,000 distinct keys). Filters support `media_type=both`; runtime bounds are objects such as `runtime_max: {"movie": 150, "tv_episode": 60}`. With both media types, hard genres must be supported by both. Provider selection is one common OR list of at most 10 IDs.

Recommendations fetch at most two source pages per media type, enrich at most 12 candidates, and stop after six eligible finalists. Scoring uses explicit preference match (0.45), inferred taste (0.35), and rating confidence (0.20), redistributing inactive weights. Rating confidence uses prior 6/10 and 100 votes. Selection applies genre diversity and a soft bonus for an unrepresented media type; it does not guarantee both types. The server retains only reusable public catalogue metadata, not recommendation inputs or results.

`result_status` describes the shortlist (`full`, `limited`, `empty`); `retrieval_status` describes retrieval (`completed`, `partial`, `failed`). `search_complete` means bounded retrieval completed, not that the entire catalogue was searched. `candidate_limit_reached` reports unexamined work. A completed empty result returns 200; failure leaving no verified title returns 503. Live catalogue verification still requires `TMDB_ACCESS_TOKEN`.

## Reliability, privacy, and attribution

About & Credits includes an unmodified [official TMDB logo](https://www.themoviedb.org/about/logos-attribution), the required notice, and JustWatch attribution. Regional availability includes timestamps and remains reported data, not a playback guarantee. Provider lookup failures and stale results are labelled. A rejected server credential now returns a safe `TOKEN_INVALID` error without echoing upstream messages or tokens.

Personal recommendation and batch responses, error responses, and `/health` use `Cache-Control: no-store`. Application request logs, when INFO logging is enabled, record only generated request IDs, endpoint names, status codes, and durations, never request bodies or credentials. The development server additionally prints normal access lines; any hosting access logs should avoid query strings and body logging. Public metadata alone is cached in process memory. Browser records are tied to the origin (including port), so lists on ports 5000 and 5002 are separate. Posters load directly from TMDB's image service; About/Preferences explains this. Browser policies restrict scripts/connections to this origin, permit TMDB images, and suppress referrers.

An optional bounded, process-local rate limiter enforces 60 catalogue API requests and 10 recommendation requests per IP per rolling minute. Enable it before a public demo:

```sh
export SCENEQUEST_RATE_LIMITS=1
```

It is disabled by default for local development. Rejections return 429, `Retry-After`, and a request ID. `/health` and static files are excluded. The limiter uses the socket address and ignores untrusted forwarded headers. Configure a known hosting proxy explicitly during deployment; do not enable arbitrary forwarded-IP trust. Limits and cache are local to one process, and limiter bookkeeping holds at most 4,096 address/category entries. Distributed limits and sustained load testing are deferred.

Changing environment variables or Python code requires restarting Flask when debug/reload is off. Existing shell exports remain available after Ctrl+C.

## Verify

```sh
.venv/bin/python -m unittest discover -s tests -v
npm test
```

JavaScript storage and taste-profile tests use Node.js 18 or newer and require no npm dependencies. The browser application uses native JavaScript modules and needs no frontend build step.

The tests stub TMDB and require no token. With a token configured and Flask running, check the live integration with:

```sh
curl 'http://127.0.0.1:5000/api/v1/config'
curl 'http://127.0.0.1:5000/api/v1/search?media_type=movie&q=arrival'
curl 'http://127.0.0.1:5000/api/v1/discover?media_type=tv&runtime_max=45'
curl 'http://127.0.0.1:5000/api/v1/titles/movie/329865?region=MY'
```

### Live integration check — 2026-10-06

Checked the configured local server on port 5002. These are observations from the first measured request and its immediate repeat, not performance guarantees. The running server used the existing recommendation implementation; the new Python reliability changes require a restart.

| Request | First measured | Repeat with warm public metadata | Outcome |
|---|---|---|---|
| Movie search for Arrival | 0.719 s | 0.007 s | 200, 20 titles |
| MY movie recommendations, provider 8/subscription, rating ≥6, runtime ≤150 minutes | 4.645 s | 0.008 s | 200, three titles, completed |

Both shortlist responses used `no-store`. Every returned title satisfied the active country, provider/access type, rating, and runtime constraints. The repeat demonstrates reuse of public metadata rather than caching personalized results.

## Accessibility review — 2026-10-06

Reviewed the interface in the in-app browser with an isolated stub catalogue. Keyboard checks covered the skip link, movie/TV arrow keys and Home, Enter to open titles, Tab/Shift+Tab wrapping inside dialogs, Escape to close, focus returning to refreshed title controls, and Cancel receiving initial focus in the reset dialog. No personal data was cleared during the review.

Title actions include the title and media type in their accessible names. Cards use level-two headings. Explicit view changes focus the page heading. Multiple-selection fields reference keyboard instructions. Results use atomic status announcements; cancelling a recommendation clears its busy state. Dialog content is no longer announced wholesale on each feedback action. Mobile navigation wraps rather than clipping offscreen controls, touch buttons are at least 44px high, and the selected country remains visible.

At a 320px viewport there was no horizontal page overflow; preferences reflowed and scrolled vertically. Measured contrast ratios were 17.58:1 for main text, 9.35:1 for muted text, and 3.89:1 for input borders against their surface. The reviewed pages reported no browser console errors. Existing Python/JavaScript regression tests remained green (60 tests).

This is a focused accessibility review, not a formal conformance certification. Testing with actual screen readers, additional browsers, and user testing remain useful before a public release.

## Delivery progress

- [x] TMDB client, normalization, configuration, and Flask foundation.
- [x] Search, discovery, details, and regional provider choices.
- [x] Browser interface, versioned storage, watchlist, history, and feedback.
- [x] Taste profile and recommendation service.
- [x] Attribution, remaining reliability work, and privacy checks.
- [x] Accessibility review and keyboard/mobile fixes.
- [ ] Optional public demo deployment.

The local MVP milestones are complete. Public deployment remains optional and requires a production WSGI server, HTTPS, rate limits enabled, and configuration for the hosting platform’s trusted proxy.

The latest overall assessment and remaining release work are recorded in [QA_REVIEW.md](QA_REVIEW.md).
