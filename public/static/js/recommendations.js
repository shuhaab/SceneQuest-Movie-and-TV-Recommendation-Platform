import {api} from './api.js';
import {tasteProfile, exclusions} from './profile.js';

export function recommendationScreen({store, getGenres, languages, render, report, isActive}) {
  const $ = id => document.getElementById(id);
  let titles = [], controller, providerController, signature = '', requestNumber = 0;
  const selected = select => [...select.selectedOptions].map(option => option.value);
  const fill = (select, choices, values = []) => {
    select.replaceChildren(...choices.map(([value, label]) => {
      const option = document.createElement('option'); option.value = String(value); option.textContent = label;
      option.selected = values.includes(String(value)); return option;
    }));
  };
  function cancel() {controller?.abort(); requestNumber++; $('recommend-submit').disabled = false; $('results').setAttribute('aria-busy', 'false');}
  function genreOptions() {
    const media = $('recommend-media').value, values = selected($('recommend-genres'));
    fill($('recommend-genres'), getGenres().filter(g => media === 'both' ? g.tmdb_ids.movie && g.tmdb_ids.tv : g.tmdb_ids[media]).map(g => [g.key, g.label]), values);
  }
  async function providers() {
    providerController?.abort(); const abort = providerController = new AbortController();
    const media = $('recommend-media').value, prefs = store.getState().preferences;
    const types = media === 'both' ? ['movie', 'tv'] : [media];
    const values = [...new Set(types.flatMap(type => prefs.provider_ids[type]))].map(String);
    fill($('recommend-providers'), values.map(id => [id, `Provider ${id}`]), values);
    $('recommend-provider-status').textContent = 'Loading provider choices…';
    const results = await Promise.allSettled(types.map(type => api('providers', {params: {media_type: type, region: prefs.region}, signal: abort.signal})));
    if (abort.signal.aborted) return;
    const choices = new Map(values.map(id => [id, `Provider ${id}`]));
    for (const result of results) if (result.status === 'fulfilled') for (const p of result.value.providers) choices.set(String(p.id), p.name);
    const current = selected($('recommend-providers'));
    fill($('recommend-providers'), [...choices].sort((a,b) => a[1].localeCompare(b[1])), current);
    $('recommend-provider-status').textContent = results.some(r => r.status === 'rejected') ? 'Provider choices unavailable. Saved selections remain available.' : 'Choose up to 10 providers. A title may match any selected provider.';
  }
  function inputs() {
    const state = store.getState(), form = $('recommend-form'), prefs = state.preferences;
    const filters = {media_type: $('recommend-media').value, region: prefs.region, display_language: prefs.display_language,
      genre_keys: selected($('recommend-genres')), provider_ids: selected($('recommend-providers')).map(Number), access_type: form.elements.access_type.value};
    if (filters.provider_ids.length > 10) throw new Error('Select at most 10 providers before generating recommendations.');
    for (const key of ['rating_min', 'year_from', 'year_to']) if (form.elements[key].value) filters[key] = Number(form.elements[key].value);
    if (form.elements.original_language.value) filters.original_language = form.elements.original_language.value;
    const runtime = {};
    if (form.elements.movie_runtime.value && filters.media_type !== 'tv') runtime.movie = Number(form.elements.movie_runtime.value);
    if (form.elements.tv_runtime.value && filters.media_type !== 'movie') runtime.tv_episode = Number(form.elements.tv_runtime.value);
    if (Object.keys(runtime).length) filters.runtime_max = runtime;
    return {filters, preferences: {preferred_genres: prefs.preferred_genres, preferred_original_languages: prefs.preferred_original_languages},
      taste_profile: tasteProfile(state.history), exclude: exclusions(state, $('include-watchlist').checked)};
  }
  function show() {
    cancel(); titles = []; signature = '';
    $('recommend-form').reset(); $('recommend-media').value = store.getState().preferences.media_choice;
    genreOptions(); providers();
    fill($('recommend-language'), [['', 'Any original language'], ...languages]);
    render([], 'Find something to watch', 'Choose filters, then generate up to three recommendations using your preferences and feedback.');
    report('Watched and dismissed titles are excluded. Watchlist titles are excluded unless you include them.');
  }
  async function generate() {
    cancel(); const number = requestNumber, abort = controller = new AbortController();
    let body;
    try {body = inputs();} catch (error) {report(error.message); return;}
    titles = []; render([], 'Finding recommendations…', 'Checking title details and regional availability.');
    $('recommend-submit').disabled = true;
    $('results').setAttribute('aria-busy', 'true'); report('Finding recommendations…');
    signature = JSON.stringify(body);
    try {
      const data = await api('recommendations', {body, signal: abort.signal});
      if (number !== requestNumber || !isActive()) return;
      // Feedback, exclusions, or preferences may change while the request runs.
      if (signature !== JSON.stringify(inputs())) {report('Your selections changed. Generate recommendations again.'); return;}
      titles = data.shortlist;
      render(titles, 'No eligible titles found', 'Try different filters or generate again. Your filters have not been relaxed.');
      const result = {full: '3 recommendations', limited: `${titles.length} ${titles.length === 1 ? 'recommendation' : 'recommendations'} · Limited matches`, empty: 'No matches in this search'}[data.result_status];
      report(`${result} · ${data.retrieval_status === 'completed' ? 'Search completed' : 'Retrieval incomplete'}${data.candidate_limit_reached ? ' · More catalogue titles may exist' : ''}${data.warnings.length ? ` · ${data.warnings.join(' ')}` : ''}`);
    } catch (error) {
      if (error.name === 'AbortError' || number !== requestNumber || !isActive()) return;
      render([], error.code === 'INVALID_FILTER' ? 'Check your filters' : 'Recommendations unavailable', error.code === 'INVALID_FILTER' ? error.message : 'No shortlist could be verified. You can still use your saved lists.');
      report(error.code === 'INVALID_FILTER' || ['TOKEN_MISSING', 'TOKEN_INVALID', 'RATE_LIMITED'].includes(error.code) ? error.message : 'Retrieval incomplete · Try generating recommendations again.');
    } finally {if (number === requestNumber) {$('recommend-submit').disabled = false; $('results').setAttribute('aria-busy', 'false');}}
  }
  function changed() {
    if (!isActive() || !signature) return;
    let body;
    try {body = inputs();} catch (error) {cancel(); report(error.message); return;}
    if (signature !== JSON.stringify(body)) {
      cancel(); const excluded = new Set(body.exclude.map(item => `${item.media_type}:${item.tmdb_id}`));
      titles = titles.filter(title => !excluded.has(title.key));
      render(titles, 'Generate a new shortlist', 'Your preferences, feedback, or excluded titles changed.');
      report('Your selections changed. Generate recommendations again.');
    } else if (titles.length) render(titles, '', '');
  }
  $('recommend-form').addEventListener('submit', event => {event.preventDefault(); generate();});
  $('recommend-form').addEventListener('change', changed);
  $('recommend-media').addEventListener('change', () => {genreOptions(); providers();});
  return {show, generate, changed, cancel};
}
