// Versioned, smoothed affinities from the latest distinct explicit feedback.
export function tasteProfile(history) {
  const latest = new Map();
  for (const entry of history) {
    const previous = latest.get(entry.key);
    if (!previous || entry.action_at > previous.action_at) latest.set(entry.key, entry);
  }
  const genres = new Map(), languages = new Map();
  let count = 0;
  function add(map, feature, sign) {
    const totals = map.get(feature) || [0, 0];
    totals[sign === 1 ? 0 : 1]++; map.set(feature, totals);
  }
  for (const entry of latest.values()) {
    if (!['liked', 'disliked'].includes(entry.sentiment)) continue;
    const features = [...new Set(entry.snapshot?.genre_keys || [])], language = entry.snapshot?.original_language;
    if (!features.length && !language) continue;
    count++;
    const sign = entry.sentiment === 'liked' ? 1 : -1;
    for (const genre of features) add(genres, genre, sign);
    if (language) add(languages, language, sign);
  }
  const weights = map => Object.fromEntries([...map].sort(([a], [b]) => a.localeCompare(b)).map(([key, [likes, dislikes]]) => [key, (likes - dislikes) / (likes + dislikes + 2)]));
  return {algorithm_version: 1, feedback_count: count, genre_affinity: weights(genres), language_affinity: weights(languages)};
}

export function exclusions(state, includeWatchlist = false) {
  const entries = [...state.history, ...state.dismissed, ...(includeWatchlist ? [] : state.watchlist)];
  const keys = [...new Set(entries.map(entry => entry.key))].sort();
  if (keys.length > 1000) throw new Error('There are more than 1,000 excluded titles. Reduce your saved lists before requesting recommendations.');
  return keys.map(key => { const [media_type, id] = key.split(':'); return {media_type, tmdb_id: Number(id)}; });
}
