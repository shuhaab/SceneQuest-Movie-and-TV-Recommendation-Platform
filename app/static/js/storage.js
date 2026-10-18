export const STORAGE_KEY = 'scenequest.state';
export const defaultState = () => ({schema_version: 1, preferences: {region: 'MY', display_language: 'en-US', media_choice: 'both', preferred_genres: [], preferred_original_languages: [], provider_ids: {movie: [], tv: []}}, watchlist: [], history: [], dismissed: []});
const copy = value => JSON.parse(JSON.stringify(value));
const dateValid = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value));
const strings = value => Array.isArray(value) && value.every(item => typeof item === 'string' && item.length <= 100);
const ids = value => Array.isArray(value) && value.length <= 10 && value.every(id => Number.isInteger(id) && id > 0 && id <= 2147483647);
const keyValid = value => typeof value === 'string' && /^(movie|tv):[1-9]\d{0,9}$/.test(value) && Number(value.split(':')[1]) <= 2147483647;
const posterValid = value => value === null || (typeof value === 'string' && /^\/[A-Za-z0-9_.-]+$/.test(value));
const onlyKeys = (value, allowed) => value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).every(key => allowed.includes(key));

export function validState(value) {
  if (!onlyKeys(value,['schema_version','preferences','watchlist','history','dismissed']) || value.schema_version !== 1) return false;
  const p = value.preferences;
  if (!onlyKeys(p,['region','display_language','media_choice','preferred_genres','preferred_original_languages','provider_ids']) || !['MY','US','GB'].includes(p.region) || p.display_language !== 'en-US' || !['movie','tv','both'].includes(p.media_choice) || !strings(p.preferred_genres) || !strings(p.preferred_original_languages) || !p.preferred_original_languages.every(v => /^[a-z]{2}$/.test(v)) || !onlyKeys(p.provider_ids,['movie','tv']) || !ids(p.provider_ids.movie) || !ids(p.provider_ids.tv)) return false;
  for (const name of ['watchlist','history','dismissed']) {
    if (!Array.isArray(value[name])) return false;
    const seen = new Set();
    for (const entry of value[name]) {
      const s = entry?.snapshot;
      if (!onlyKeys(entry, name === 'history' ? ['key','action_at','snapshot','sentiment'] : ['key','action_at','snapshot']) || !keyValid(entry?.key) || seen.has(entry.key) || !dateValid(entry.action_at) || !onlyKeys(s,['title','poster_path','genre_keys','original_language','snapshot_at']) || typeof s.title !== 'string' || !posterValid(s.poster_path) || !strings(s.genre_keys) || !(s.original_language === null || (typeof s.original_language === 'string' && /^[a-z]{2}$/.test(s.original_language))) || !dateValid(s.snapshot_at)) return false;
      if (name === 'history' && !['liked','disliked','neutral'].includes(entry.sentiment)) return false;
      seen.add(entry.key);
    }
  }
  return true;
}

function snapshot(title) {
  return {title: title.title, poster_path: title.poster_path ?? null, genre_keys: title.genre_keys || [], original_language: title.original_language ?? null, snapshot_at: dateValid(title.metadata_checked_at) ? title.metadata_checked_at : new Date().toISOString()};
}

export function createStore(getStorage = () => window.localStorage) {
  let state = defaultState(), warning = '', blocked = false;
  const listeners = new Set();
  try {
    const raw = getStorage().getItem(STORAGE_KEY);
    if (raw !== null) {
      const stored = JSON.parse(raw);
      if (!validState(stored)) throw new Error('Incompatible saved data');
      state = stored;
    }
  } catch {
    blocked = true;
    warning = 'Saved data could not be read. Changes will stay in this tab until you clear personal data. Existing saved data has been preserved.';
  }
  function notify() { for (const listener of listeners) listener(copy(state), warning); }
  function commit(next) {
    if (!validState(next)) throw new Error('This personal record is invalid.');
    state = next;
    if (!blocked) {
      try { getStorage().setItem(STORAGE_KEY, JSON.stringify(state)); warning = ''; }
      catch { warning = 'Changes are available in this tab, but could not be saved. Browser storage may be disabled or full.'; }
    }
    notify();
  }
  function entry(title) { return {key: title.key, action_at: new Date().toISOString(), snapshot: snapshot(title)}; }
  return {
    getState: () => copy(state), getWarning: () => warning,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    preferences(preferences) { commit({...copy(state), preferences: copy(preferences)}); },
    watchlist(title) {
      const next = copy(state), existing = next.watchlist.some(e => e.key === title.key);
      next.watchlist = next.watchlist.filter(e => e.key !== title.key);
      if (!existing) next.watchlist.unshift(entry(title));
      commit(next);
    },
    watched(title, sentiment = 'neutral') {
      const next = copy(state);
      next.history = next.history.filter(e => e.key !== title.key);
      next.history.unshift({...entry(title), sentiment});
      next.watchlist = next.watchlist.filter(e => e.key !== title.key);
      next.dismissed = next.dismissed.filter(e => e.key !== title.key);
      commit(next);
    },
    dismiss(title) {
      const next = copy(state);
      next.dismissed = next.dismissed.filter(e => e.key !== title.key);
      next.dismissed.unshift(entry(title));
      next.watchlist = next.watchlist.filter(e => e.key !== title.key);
      commit(next);
    },
    remove(list, key) {
      if (!['watchlist','history','dismissed'].includes(list)) throw new Error('Invalid list');
      const next = copy(state); next[list] = next[list].filter(e => e.key !== key); commit(next);
    },
    refresh(titles) {
      const next = copy(state), fresh = new Map(titles.filter(t => !t.metadata_stale).map(t => [t.key, snapshot(t)]));
      let changed = false;
      for (const list of ['watchlist','history','dismissed']) for (const e of next[list]) if (fresh.has(e.key)) { const s = fresh.get(e.key); if (JSON.stringify(s) !== JSON.stringify(e.snapshot)) {e.snapshot = s; changed = true;} }
      if (changed) commit(next);
    },
    clear() {
      state = defaultState();
      try { getStorage().removeItem(STORAGE_KEY); blocked = false; warning = ''; }
      catch { blocked = true; warning = 'Personal data was cleared in this tab, but saved data could not be removed from browser storage.'; }
      notify();
    },
  };
}

export function titleFromEntry(entry) {
  const [media_type, id] = entry.key.split(':');
  return {key: entry.key, media_type, tmdb_id: Number(id), title: entry.snapshot.title, poster_path: entry.snapshot.poster_path, genre_keys: entry.snapshot.genre_keys, original_language: entry.snapshot.original_language, metadata_checked_at: entry.snapshot.snapshot_at, snapshot_only: true};
}
