import test from 'node:test';
import assert from 'node:assert/strict';
import {tasteProfile, exclusions} from '../app/static/js/profile.js';
const entry = (key, sentiment, genres = ['drama'], language = 'en', date = '2026-01-01') => ({key, sentiment, action_at: date, snapshot: {genre_keys: genres, original_language: language}});
test('smoothed affinities keep zero evidence and skip neutral or missing features', () => {
  const result = tasteProfile([entry('movie:1', 'liked'), entry('tv:1', 'disliked'), entry('movie:2', 'neutral'), entry('movie:3', 'liked', [], null)]);
  assert.deepEqual(result, {algorithm_version: 1, feedback_count: 2, genre_affinity: {drama: 0}, language_affinity: {en: 0}});
});
test('latest distinct feedback wins including neutral replacing a like', () => {
  assert.equal(tasteProfile([entry('movie:1', 'liked'), entry('movie:1', 'disliked', ['comedy'], 'ms', '2026-02-01')]).genre_affinity.comedy, -1/3);
  assert.equal(tasteProfile([entry('movie:1', 'liked'), entry('movie:1', 'neutral', [], null, '2026-02-01')]).feedback_count, 0);
});
test('genre dimensions count each title once', () => {
  const result = tasteProfile([entry('movie:1', 'liked', ['drama', 'drama']), entry('movie:2', 'liked')]);
  assert.equal(result.genre_affinity.drama, .5);
  assert.equal(result.feedback_count, 2);
});
test('exclusions always include watched and dismissed, with opt-in watchlist', () => {
  const state = {history: [entry('movie:1', 'neutral')], dismissed: [entry('tv:1')], watchlist: [entry('movie:2'), entry('movie:1')]};
  assert.deepEqual(exclusions(state), [{media_type:'movie',tmdb_id:1},{media_type:'movie',tmdb_id:2},{media_type:'tv',tmdb_id:1}]);
  assert.equal(exclusions(state, true).length, 2);
});
test('too many exclusions are rejected without truncation', () => {
  assert.throws(() => exclusions({history: Array.from({length:1001}, (_, i) => entry(`movie:${i+1}`)), dismissed:[], watchlist:[]}), /1,000/);
});
