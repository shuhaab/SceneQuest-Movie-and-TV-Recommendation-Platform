import test from 'node:test';
import assert from 'node:assert/strict';
import {createStore, STORAGE_KEY, defaultState, validState} from '../app/static/js/storage.js';

function memoryStorage(raw = null) {
  const data = new Map(raw === null ? [] : [[STORAGE_KEY,raw]]);
  return {getItem:k => data.get(k) ?? null, setItem:(k,v) => data.set(k,v), removeItem:k => data.delete(k)};
}
const film = {key:'movie:42',title:'Example movie',poster_path:null,genre_keys:['drama'],original_language:'en'};
const show = {...film,key:'tv:42',title:'Example show'};

test('composite keys survive persistence and neutral watched feedback stays neutral', () => {
  const storage = memoryStorage(), store = createStore(() => storage);
  store.watchlist(film);store.watchlist(show);store.watched(film);
  const restored = createStore(() => storage).getState();
  assert.deepEqual(restored.watchlist.map(e => e.key),['tv:42']);
  assert.equal(restored.history[0].sentiment,'neutral');
  assert.equal(restored.history[0].snapshot.title,'Example movie');
  assert.equal(restored.history[0].snapshot.rating,undefined);
});
test('latest explicit feedback replaces earlier feedback without duplicate history', () => {
  const store = createStore(() => memoryStorage());
  store.watched(film,'liked');store.watched(film,'disliked');
  assert.equal(store.getState().history.length,1);
  assert.equal(store.getState().history[0].sentiment,'disliked');
  store.dismiss(film);store.watched(film,'neutral');
  assert.equal(store.getState().dismissed.length,0);
});
test('malformed and future-version storage is preserved until an explicit reset', () => {
  for (const raw of ['{broken',JSON.stringify({...defaultState(),schema_version:99})]) {
    const storage = memoryStorage(raw), store = createStore(() => storage);
    store.watchlist(film);
    assert.equal(storage.getItem(STORAGE_KEY),raw);
    assert.match(store.getWarning(),/preserved/);
    store.clear();store.watchlist(show);
    assert.equal(JSON.parse(storage.getItem(STORAGE_KEY)).watchlist[0].key,'tv:42');
  }
});
test('write failures retain editable state in memory and warn explicitly', () => {
  const storage = {...memoryStorage(),setItem() {throw new Error('QuotaExceeded');}};
  const store = createStore(() => storage);store.watchlist(film);store.watched(show,'liked');
  assert.equal(store.getState().watchlist.length,1);
  assert.equal(store.getState().history.length,1);
  assert.match(store.getWarning(),/could not be saved/);
});
test('disabled storage and failed clearing remain usable without overwriting records', () => {
  const store = createStore(() => {throw new Error('SecurityError');});
  store.watchlist(film);store.clear();
  assert.equal(store.getState().watchlist.length,0);
  assert.match(store.getWarning(),/could not be removed/);
});
test('refresh updates snapshots while preserving the original action and sentiment', () => {
  const store = createStore(() => memoryStorage());store.watched(film,'liked');
  const before = store.getState().history[0];
  store.refresh([{...film,title:'Updated title',metadata_checked_at:'2026-10-06T00:00:00Z'}]);
  const after = store.getState().history[0];
  assert.equal(after.action_at,before.action_at);assert.equal(after.sentiment,'liked');
  assert.equal(after.snapshot.title,'Updated title');
  store.refresh([{...film,title:'Stale title',metadata_stale:true}]);
  assert.equal(store.getState().history[0].snapshot.title,'Updated title');
});
test('invalid entries and unsafe poster URLs are rejected', () => {
  const state = defaultState();state.watchlist = [{key:'movie:0',action_at:'invalid',snapshot:{}}];
  assert.equal(validState(state),false);
  const store = createStore(() => memoryStorage());
  assert.throws(() => store.watchlist({...film,poster_path:'https://untrusted.example/image.jpg'}));
  store.watchlist(film);
  const expanded = store.getState();expanded.watchlist[0].snapshot.overview = 'Full upstream content';
  assert.equal(validState(expanded),false);
});
