import {recommendationScreen} from './recommendations.js';
import {api} from './api.js';
import {createStore, titleFromEntry} from './storage.js';

const $ = id => document.getElementById(id);
const store = createStore();
let genres = JSON.parse($('bootstrap').textContent).genres;
const languages = [['en','English'],['ms','Malay'],['zh','Chinese'],['ta','Tamil'],['ja','Japanese'],['ko','Korean'],['hi','Hindi'],['fr','French'],['es','Spanish'],['de','German'],['ar','Arabic'],['pt','Portuguese'],['th','Thai'],['id','Indonesian'],['vi','Vietnamese'],['ru','Russian'],['it','Italian']];
const countries = {MY:'Malaysia', US:'United States', GB:'United Kingdom'};
const names = {recommendations:'For you', browse:'Browse', watchlist:'Watchlist', history:'History', dismissed:'Not interested'};
let view = 'browse', media = store.getState().preferences.media_choice === 'tv' ? 'tv' : 'movie', page = 1;
let browseTitles = [], browseAbort, providerAbort, preferenceAbort, detailAbort;
let currentDetail = null, detailMessage = '', timer, requestVersion = 0;
const liveTitles = new Map();
let detailOpener = null;

function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  if (className) result.className = className;
  return result;
}
function button(label, callback, className) {
  const result = node('button', label, className);
  result.type = 'button'; result.addEventListener('click', callback); return result;
}
const selected = select => [...select.selectedOptions].map(option => option.value);
function fill(select, items, values = []) {
  select.replaceChildren();
  for (const [value, label] of items) {
    const option = node('option', label); option.value = String(value); option.selected = values.includes(String(value)); select.append(option);
  }
}
function preserveFocus(callback) {
  const active = document.activeElement, action = active?.dataset.action, key = active?.dataset.key, dialog = active?.closest('dialog'), activeId = active?.id;
  callback();
  if (activeId && !document.contains(active)) $(activeId)?.focus({preventScroll:true});
  if (action) {
    const next = [...document.querySelectorAll('[data-action]')].find(el => el.dataset.action === action && el.dataset.key === key && el.closest('dialog') === dialog);
    if (next && !next.disabled) next.focus({preventScroll:true}); else if (!document.contains(active)) (dialog?.querySelector('h2') || $('page-title')).focus({preventScroll:true});
  }
}
function dateText(value) { const d = new Date(typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00` : value); return Number.isFinite(d.getTime()) ? d.toLocaleDateString(undefined, {year:'numeric',month:'short',day:'numeric'}) : 'Unknown'; }
function timeText(value) { const d = new Date(value); return Number.isFinite(d.getTime()) ? d.toLocaleString(undefined,{year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}) : 'Unknown'; }
function report(text, retry = false) { $('result-status').textContent = text; $('retry').hidden = !retry; }
function storageNotice() {
  const warning = store.getWarning(); $('storage-warning').hidden = !warning; $('storage-warning').replaceChildren();
  if (warning) $('storage-warning').append(node('span', warning), button('Clear personal data', () => $('reset-dialog').showModal()));
}
function poster(title, className = '') {
  if (typeof title.poster_path === 'string' && /^\/[A-Za-z0-9_.-]+$/.test(title.poster_path)) {
    const img = node('img', undefined, className); img.src = `https://image.tmdb.org/t/p/w342${title.poster_path}`;
    img.alt = ''; img.loading = 'lazy'; img.addEventListener('error', () => img.replaceWith(poster({...title,poster_path:null}, className)), {once:true}); return img;
  }
  const placeholder = node('div', undefined, `poster-empty ${className}`);
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg'); svg.setAttribute('viewBox','0 0 32 32'); svg.setAttribute('aria-hidden','true');
  const path = document.createElementNS('http://www.w3.org/2000/svg','path'); path.setAttribute('d','M5 5h22v22H5z M10 5v22 M22 5v22 M5 11h5 M5 21h5 M22 11h5 M22 21h5'); path.setAttribute('fill','none'); path.setAttribute('stroke','currentColor'); path.setAttribute('stroke-width','1.5'); svg.append(path);
  placeholder.append(svg,node('span','No poster')); return placeholder;
}
function action(label, name, title, callback, pressed) {
  const result = button(label, () => { try { callback(); } catch (error) {report(error.message);} });
  result.dataset.action = name; result.dataset.key = title.key;
  result.setAttribute('aria-label', `${label}: ${title.title} (${title.media_type === 'tv' ? 'TV' : 'Movie'})`);
  if (pressed !== undefined) result.setAttribute('aria-pressed', String(pressed));
  return result;
}
function actions(title, detail = false) {
  const state = store.getState(), listed = state.watchlist.some(e => e.key === title.key), history = state.history.find(e => e.key === title.key), dismissed = state.dismissed.some(e => e.key === title.key);
  const group = node('div', undefined, detail ? 'detail-actions' : 'card-actions');
  if (view === 'dismissed' && !detail) {group.append(action('Undo', 'undo', title, () => store.remove('dismissed',title.key))); return group;}
  if (view !== 'history' || detail) group.append(action(listed ? 'Saved' : '+ Watchlist', 'save', title, () => store.watchlist(title), listed));
  if (view !== 'history' || detail) {
    const watched = action(history ? 'Watched' : 'Mark watched', 'watched', title, () => store.watched(title)); watched.disabled = Boolean(history); group.append(watched);
  }
  if (view === 'history' || detail) for (const sentiment of ['liked','disliked']) {
    group.append(action(sentiment === 'liked' ? 'Liked' : 'Disliked', sentiment, title,
      () => store.watched(title, history?.sentiment === sentiment ? 'neutral' : sentiment), history?.sentiment === sentiment));
  }
  if (view === 'history' && !detail) group.append(action('Remove', 'remove', title, () => store.remove('history',title.key)));
  if (detail) group.append(action(dismissed ? 'Undo not interested' : 'Not interested', 'dismiss', title, () => dismissed ? store.remove('dismissed',title.key) : store.dismiss(title), dismissed));
  return group;
}
function card(title) {
  const article = node('article', undefined, 'title-card');
  article.setAttribute('aria-label', `${title.title} (${title.media_type === 'tv' ? 'TV' : 'Movie'})`);
  const open = button('', () => openDetail(title), 'poster-button'); open.setAttribute('aria-label',`Details: ${title.title}`); open.dataset.action = 'details-poster'; open.dataset.key = title.key; open.append(poster(title));
  const heading = node('h2'), titleButton = button(title.title, () => openDetail(title), 'text-button');
  titleButton.dataset.action = 'details-title'; titleButton.dataset.key = title.key; heading.append(titleButton);
  const info = [title.media_type === 'tv' ? 'TV' : 'Movie'];
  if (title.first_release_date) info.push(title.first_release_date.slice(0,4));
  if (title.rating?.average !== null && title.rating?.average !== undefined) info.push(`${title.rating.average.toFixed(1)} / 10`);
  article.append(open,heading,node('p',info.join(' · '),'title-meta'));
  if (title.snapshot_only) article.append(node('p',`Saved snapshot · ${dateText(title.metadata_checked_at)}`,'title-meta'));
  else if (title.rating?.votes !== null && title.rating?.votes !== undefined) article.append(node('p',`${title.rating.votes.toLocaleString()} votes`,'title-meta'));
  if (view === 'history') {
    const sentiment = store.getState().history.find(e => e.key === title.key)?.sentiment;
    article.append(node('p',sentiment === 'neutral' ? 'Watched · No rating yet' : sentiment === 'liked' ? 'Watched · Liked' : 'Watched · Disliked','title-meta'));
  }
  if (view === 'recommendations' && title.reasons?.length) {const list = node('ul', undefined, 'recommendation-reasons'); for (const reason of title.reasons) list.append(node('li', reason)); article.append(list);}
  article.append(actions(title)); return article;
}
function renderGrid(titles, emptyTitle, emptyText) {
  preserveFocus(() => {
    $('results').replaceChildren(...titles.map(card));
    if (!titles.length) {const empty = node('div',undefined,'empty-state'); empty.append(node('h2',emptyTitle),node('p',emptyText)); $('results').append(empty);}
  });
}
function libraryEntries() {return store.getState()[view] || [];}
function renderLibrary() {
  const entries = libraryEntries(); page = Math.min(page, Math.max(1,Math.ceil(entries.length / 20)));
  const titles = entries.slice((page-1)*20,page*20).map(e => liveTitles.get(e.key) || titleFromEntry(e));
  $('page-context').textContent = `${entries.length} saved ${entries.length === 1 ? 'title' : 'titles'}`;
  const messages = {watchlist:['Your watchlist is empty','Save a title while browsing to find it here.'], history:['No viewing history yet','Mark a title watched, then add a like or dislike if you want. Watched alone does not indicate a preference.'], dismissed:['No dismissed titles','Titles marked “Not interested” appear here. You can undo that choice at any time.']};
  renderGrid(titles,...messages[view]); setPagination(entries.length > page*20); return titles;
}
function setPagination(hasNext) { $('previous').disabled = page <= 1; $('next').disabled = !hasNext; $('page-number').textContent = `Page ${page}`; }
function filterParams() {
  const params = {}; for (const [key,value] of new FormData($('filter-form'))) if (value) {if (key === 'genre_keys' || key === 'provider_ids') (params[key] ||= []).push(value); else params[key] = value;}
  return {...params,media_type:media,page,region:store.getState().preferences.region,display_language:'en-US'};
}
function abortWork() {recommendations.cancel();browseAbort?.abort(); requestVersion++; clearTimeout(timer); $('results').setAttribute('aria-busy','false');}
async function loadBrowse() {
  if (view !== 'browse') return;
  $('page-context').textContent = countries[store.getState().preferences.region];
  abortWork(); const version = requestVersion, controller = browseAbort = new AbortController();
  const query = $('search').value.trim(), searching = Boolean(query);
  $('clear-search').hidden = !searching; $('filter-fields').disabled = searching; $('search-help').hidden = !searching;
  $('results').setAttribute('aria-busy','true'); report(searching ? `Searching ${media === 'movie' ? 'movies' : 'TV shows'}…` : 'Loading titles…');
  $('previous').disabled = true; $('next').disabled = true;
  try {
    const data = await api(searching ? 'search' : 'discover', {params:searching ? {q:query,media_type:media,page,display_language:'en-US'} : filterParams(),signal:controller.signal});
    if (version !== requestVersion || view !== 'browse') return;
    browseTitles = data.results;
    renderGrid(browseTitles,searching ? 'No titles found in this search' : 'No matches found on this page',searching ? 'Try a different title or switch between Movies and TV shows.' : 'Try another source page, fewer filters, or a longer runtime. Filters have not been relaxed.');
    report(`${browseTitles.length} ${browseTitles.length === 1 ? 'title' : 'titles'}${searching ? ` matching “${query}”` : ` · ${countries[store.getState().preferences.region]}`}${data.warnings?.length ? ` · ${data.warnings.join(' ')}` : ''}`);
    setPagination(searching ? page < Math.min(data.total_pages,500) : data.has_more_source_pages);
  } catch (error) {
    if (error.name === 'AbortError' || version !== requestVersion) return;
    browseTitles = [];
    renderGrid([], error.code === 'INVALID_FILTER' ? 'Check your filters' : 'Catalogue unavailable', error.code === 'INVALID_FILTER' ? 'Adjust your filters and apply them again.' : 'You can still open and edit your saved lists.');
    report(error.message,true); setPagination(false);
  } finally {if (version === requestVersion) $('results').setAttribute('aria-busy','false');}
}
async function refreshLibrary() {
  abortWork(); const version = requestVersion, controller = browseAbort = new AbortController(), titles = renderLibrary();
  if (!titles.length) {report('Your lists are saved in this browser.'); return;}
  report('Saved snapshots · Checking visible titles…');
  try {
    const data = await api('titles/batch',{signal:controller.signal,body:{titles:titles.map(t => ({media_type:t.media_type,tmdb_id:t.tmdb_id})),region:store.getState().preferences.region,display_language:'en-US'}});
    if (version !== requestVersion || view === 'browse') return;
    for (const title of data.titles) liveTitles.set(title.key,title);
    store.refresh(data.titles); renderLibrary();
    report(data.errors.length || data.titles.some(t => t.warnings?.length) ? 'Some details could not be refreshed. Saved snapshots remain available; open a title for availability status.' : 'Visible title metadata refreshed.');
  } catch (error) {if (error.name !== 'AbortError' && version === requestVersion) report('Catalogue unavailable · Showing saved snapshots. You can still edit this list.',true);}
}
function switchView(next) {
  abortWork(); view = next; page = 1;
  document.querySelectorAll('[data-view]').forEach(el => el.setAttribute('aria-current',el.dataset.view === view ? 'page' : 'false'));
  $('page-title').textContent = names[view]; $('browse-controls').hidden = view !== 'browse';
  $('recommend-controls').hidden = view !== 'recommendations';
  document.querySelector('.pagination').hidden = view === 'recommendations';
  $('results').setAttribute('role',view === 'browse' ? 'tabpanel' : 'region');
  $('results').setAttribute('aria-labelledby',view === 'browse' ? `${media}-tab` : 'page-title');
  if (view === 'browse') {$('page-context').textContent = countries[store.getState().preferences.region]; loadBrowse();} else if (view === 'recommendations') {$('page-context').textContent = countries[store.getState().preferences.region]; recommendations.show();} else refreshLibrary();
}
function mediaOptions() {
  fill($('genres'),genres.filter(g => g.tmdb_ids[media]).map(g => [g.key,g.label]));
  $('search').placeholder = media === 'movie' ? 'Search movie titles' : 'Search TV titles';
  $('runtime-hint').textContent = media === 'movie' ? 'Minutes' : 'Median episode minutes';
  document.querySelectorAll('[data-media]').forEach(el => {const active = el.dataset.media === media;el.setAttribute('aria-selected',String(active));el.tabIndex = active ? 0 : -1;});
  $('results').setAttribute('aria-labelledby',`${media}-tab`);
}
async function loadProviders() {
  providerAbort?.abort(); const controller = providerAbort = new AbortController(), prefs = store.getState().preferences;
  const ids = prefs.provider_ids[media].map(String), selectedMedia = media;
  fill($('providers'),ids.map(id => [id,`Provider ${id}`]),ids); $('provider-status').textContent = 'Loading provider choices…';
  try {
    const data = await api('providers',{params:{media_type:media,region:prefs.region},signal:controller.signal});
    if (controller.signal.aborted || media !== selectedMedia) return;
    const current = selected($('providers')), choices = data.providers.map(p => [String(p.id),p.name]);
    for (const id of current) if (!choices.some(p => p[0] === id)) choices.push([id,`Provider ${id} (not listed)`]);
    fill($('providers'),choices,current); $('provider-status').textContent = data.providers.length ? '' : 'No provider choices were reported for this country.';
  } catch (error) {if (error.name !== 'AbortError') $('provider-status').textContent = 'Provider choices are unavailable. Saved provider filters remain selected.';}
}
async function openDetail(title) {
  const opener = document.activeElement;
  detailOpener = {element: opener, action: opener?.dataset.action, key: title.key};
  detailAbort?.abort(); const controller = detailAbort = new AbortController(); currentDetail = title; detailMessage = 'Loading details…'; renderDetail();
  if (!$('title-dialog').open) $('title-dialog').showModal();
  try {
    const data = await api(`titles/${title.media_type}/${title.tmdb_id}`,{params:{region:store.getState().preferences.region,display_language:'en-US'},signal:controller.signal});
    if (controller.signal.aborted) return; currentDetail = data; detailMessage = data.warnings.join(' '); liveTitles.set(data.key,data); store.refresh([data]); renderDetail();
  } catch (error) {if (error.name !== 'AbortError' && !controller.signal.aborted) {detailMessage = 'Details are unavailable. Saved title information and list edits remain available.';renderDetail();}}
}
function renderDetail() {
  if (!currentDetail) return;
  preserveFocus(() => {
    const title = currentDetail, layout = node('div',undefined,'detail-layout'), header = node('div');
    const heading = node('h2',title.title);heading.id = 'detail-title'; heading.tabIndex = -1;header.append(heading,node('p',title.media_type === 'tv' ? 'TV show' : 'Movie','muted'));
    layout.append(poster(title,'detail-poster'),header);
    const content = node('div',undefined,'detail-copy');
    const status = node('p', detailMessage, 'muted'); status.setAttribute('role', 'status'); status.setAttribute('aria-atomic', 'true'); content.append(status);
    content.append(node('p',title.overview || (title.snapshot_only ? 'Synopsis is unavailable in a saved snapshot.' : 'No synopsis available.')));
    const facts = node('dl',undefined,'detail-facts');
    const entries = [['Genres',(title.genre_keys || []).map(key => genres.find(g => g.key === key)?.label || key).join(', ') || 'Unknown'],['First release',title.first_release_date ? dateText(title.first_release_date) : 'Unknown'],['Rating',title.rating?.average != null ? `${title.rating.average.toFixed(1)} / 10 · ${title.rating.votes == null ? 'Vote count unknown' : `${title.rating.votes.toLocaleString()} votes`}` : 'Unknown'],[title.media_type === 'tv' ? 'Episode runtime' : 'Runtime',title.runtime?.minutes ? `${title.runtime.minutes} minutes${title.media_type === 'tv' ? ' (median, varies by episode)' : ''}` : 'Unknown'],['Metadata checked',`${timeText(title.metadata_checked_at)}${title.metadata_stale ? ' · Stale' : ''}`]];
    for (const [label,value] of entries) facts.append(node('dt',label),node('dd',value));
    content.append(facts,actions(title,true));
    const availability = node('section',undefined,'availability');availability.append(node('h3',`Where to watch · ${countries[store.getState().preferences.region]}`));
    const a = title.availability;
    if (!a?.providers) availability.append(node('p','Regional availability is unavailable.','muted'));
    else {
      const list = node('ul'), labels = {flatrate:'Subscription',free:'Free',ads:'With ads',rent:'Rent',buy:'Buy'};
      for (const [mode,label] of Object.entries(labels)) if (a.providers[mode]?.length) list.append(node('li',`${label}: ${a.providers[mode].map(p => p.name).join(', ')}`));
      if (list.children.length) availability.append(list); else availability.append(node('p','No providers were reported for this country.','muted'));
      availability.append(node('p',`${a.stale ? 'Stale availability · ' : ''}Checked ${timeText(a.checked_at)}. Reported by TMDB / JustWatch; confirm availability before watching.`,'muted'));
      if (a.watch_url) {const link = node('a','Check on TMDB');link.href = a.watch_url;link.target = '_blank';link.rel = 'noopener noreferrer';availability.append(link);}
    }
    content.append(availability);layout.append(content);$('detail-content').replaceChildren(layout);
  });
}
async function preferenceProviders(reset = false) {
  preferenceAbort?.abort(); const controller = preferenceAbort = new AbortController(), p = store.getState().preferences, region = $('preference-region').value;
  $('save-preferences').disabled = true; $('preference-status').textContent = 'Loading provider choices…';
  const results = await Promise.allSettled(['movie','tv'].map(async type => {
    const values = !reset && region === p.region ? p.provider_ids[type].map(String) : [];
    fill($(`${type}-providers`),values.map(id => [id,`Provider ${id}`]),values);
    const data = await api('providers',{params:{media_type:type,region},signal:controller.signal});
    if (controller.signal.aborted) return;
    const choices = data.providers.map(item => [String(item.id),item.name]);
    for (const id of values) if (!choices.some(item => item[0] === id)) choices.push([id,`Provider ${id} (not listed)`]);
    fill($(`${type}-providers`),choices,values);
  }));
  if (controller.signal.aborted) return;
  $('save-preferences').disabled = false;
  $('preference-status').textContent = results.some(r => r.status === 'rejected') ? 'Some provider choices are unavailable. Existing selections for the same country are preserved.' : 'Use Ctrl or Command to select more than one option.';
}
function openPreferences() {
  const p = store.getState().preferences, form = $('preferences-form');
  for (const name of ['media_choice','region','display_language']) form.elements[name].value = p[name];
  fill($('preferred-genres'),genres.map(g => [g.key,g.label]),p.preferred_genres);
  const choices = [...languages];for (const lang of p.preferred_original_languages) if (!choices.some(c => c[0] === lang)) choices.push([lang,lang]);
  fill($('preferred-languages'),choices,p.preferred_original_languages);
  $('preferences-dialog').showModal(); preferenceProviders();
}

const recommendations = recommendationScreen({store, getGenres: () => genres, languages, render: renderGrid, report, isActive: () => view === 'recommendations'});

document.querySelectorAll('[data-view]').forEach(el => el.addEventListener('click',() => {switchView(el.dataset.view); $('page-title').focus();}));
document.querySelectorAll('[data-media]').forEach(el => {
  el.addEventListener('click',() => {media = el.dataset.media;page = 1;browseTitles = [];mediaOptions();loadProviders();loadBrowse();});
  el.addEventListener('keydown',event => {if (['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) {event.preventDefault();const next = event.key === 'Home' ? $('movie-tab') : event.key === 'End' ? $('tv-tab') : $('movie-tab') === el ? $('tv-tab') : $('movie-tab');next.click();next.focus();}});
});
$('search-form').addEventListener('submit',event => {event.preventDefault();page = 1;loadBrowse();});
$('search').addEventListener('input',() => {abortWork();page = 1;timer = setTimeout(loadBrowse,300);});
$('clear-search').addEventListener('click',() => {$('search').value = '';page = 1;loadBrowse();$('search').focus();});
$('filter-form').addEventListener('submit',event => {event.preventDefault();page = 1;loadBrowse();});
$('reset-filters').addEventListener('click',() => {$('filter-form').reset();page = 1;loadBrowse();});
$('previous').addEventListener('click',() => {page--;view === 'browse' ? loadBrowse() : view === 'recommendations' ? recommendations.show() : refreshLibrary();});
$('next').addEventListener('click',() => {page++;view === 'browse' ? loadBrowse() : view === 'recommendations' ? recommendations.show() : refreshLibrary();});
$('retry').addEventListener('click',() => view === 'browse' ? loadBrowse() : view === 'recommendations' ? recommendations.generate() : refreshLibrary());
$('open-preferences').addEventListener('click',openPreferences);
$('preference-region').addEventListener('change',() => preferenceProviders(true));
$('preferences-form').addEventListener('submit',event => {
  event.preventDefault();const form = event.currentTarget;
  try {store.preferences({region:form.elements.region.value,display_language:form.elements.display_language.value,media_choice:form.elements.media_choice.value,preferred_genres:selected($('preferred-genres')),preferred_original_languages:selected($('preferred-languages')),provider_ids:{movie:selected($('movie-providers')).map(Number),tv:selected($('tv-providers')).map(Number)}});}
  catch { $('preference-status').textContent = 'Choose at most 10 providers for each media type.'; return; }
  liveTitles.clear();$('preferences-dialog').close();page = 1;loadProviders();view === 'browse' ? loadBrowse() : view === 'recommendations' ? recommendations.show() : refreshLibrary();
});
$('open-about').addEventListener('click',event => {event.preventDefault();$('about-dialog').showModal();});
document.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click',() => $(el.dataset.close).close()));
$('title-dialog').addEventListener('close',() => {
  detailAbort?.abort(); currentDetail = null;
  const opener = detailOpener; detailOpener = null;
  const replacement = [...document.querySelectorAll('#results [data-action]')].find(el => el.dataset.action === opener?.action && el.dataset.key === opener?.key);
  (opener?.element?.isConnected ? opener.element : replacement || $('page-title')).focus({preventScroll:true});
});
$('preferences-dialog').addEventListener('close',() => preferenceAbort?.abort());
$('open-reset').addEventListener('click',() => $('reset-dialog').showModal());
$('confirm-reset').addEventListener('click',() => {store.clear();liveTitles.clear();$('reset-dialog').close();if ($('preferences-dialog').open) {$('preferences-dialog').close();}page = 1;loadProviders();view === 'browse' ? loadBrowse() : view === 'recommendations' ? recommendations.show() : refreshLibrary();});
store.subscribe(() => {
  storageNotice();if (view === 'browse') {if (browseTitles.length) renderGrid(browseTitles,'','');} else if (view === 'recommendations') recommendations.changed(); else renderLibrary();
  if ($('title-dialog').open) renderDetail();
});
fill($('original-language'),[['','Any original language'],...languages]);mediaOptions();loadProviders();storageNotice();switchView('browse');
api('config').then(data => {genres = data.genres;}).catch(() => {});

// Keep Tab within the topmost modal, including after asynchronous content refreshes.
document.addEventListener('keydown', event => {
  if (event.key !== 'Tab') return;
  const modal = [...document.querySelectorAll('dialog[open]')].at(-1);
  if (!modal) return;
  const controls = [...modal.querySelectorAll('button, a[href], input, select, textarea, [tabindex]')]
    .filter(el => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length);
  if (!controls.length) return;
  const first = controls[0], last = controls.at(-1), active = document.activeElement;
  if (event.shiftKey && (active === first || !controls.includes(active))) {event.preventDefault(); last.focus();}
  else if (!event.shiftKey && (active === last || !controls.includes(active))) {event.preventDefault(); first.focus();}
});
