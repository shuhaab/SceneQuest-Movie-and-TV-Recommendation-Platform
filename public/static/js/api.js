export async function api(path, {params = {}, signal, body} = {}) {
  const url = new URL(`/api/v1/${path}`, window.location.origin);
  for (const [key, value] of Object.entries(params)) {
    if (value !== '' && value !== undefined && value !== null) url.searchParams.set(key, Array.isArray(value) ? value.join(',') : String(value));
  }
  const response = await fetch(url, {signal, method: body ? 'POST' : 'GET',
    headers: body ? {'Content-Type': 'application/json'} : {}, body: body ? JSON.stringify(body) : undefined});
  let data;
  try { data = await response.json(); } catch { throw new Error('The catalogue returned an unreadable response. Please try again.'); }
  if (!response.ok) {
    const labels = {year_from:'From year',year_to:'To year',rating_min:'Minimum rating',rating_max:'Maximum rating',runtime_min:'Minimum runtime',runtime_max:'Maximum runtime',provider_ids:'Providers',genre_keys:'Genres',original_language:'Original language',display_language:'Display language',media_type:'Media type',region:'Country',access_type:'Access type',page:'Page',q:'Title search'};
    const fields = Object.entries(data.error?.fields || {}).map(([key,value]) => {
      let message = String(value);
      for (const [field,label] of Object.entries(labels)) message = message.replaceAll(field,label.toLowerCase());
      return `${labels[key.split('.').at(-1)] || 'Selection'}: ${message}`;
    }).join(' ');
    const message = response.status === 422 ? `Check your filters. ${fields}`
      : response.status === 429 ? 'Too many requests. Wait a minute and try again.'
      : data.error?.code === 'TOKEN_MISSING' ? 'Catalogue access is not configured. Set the server TMDB access token and restart Flask.'
      : data.error?.code === 'TOKEN_INVALID' ? 'TMDB rejected the server credential. Check the API Read Access Token and restart Flask.'
      : 'The catalogue is temporarily unavailable. Your saved lists are still available.';
    const error = new Error(message);
    error.code = data.error?.code;
    throw error;
  }
  return data;
}
