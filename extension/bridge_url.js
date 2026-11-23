// The bridge URL rules (beta4-polish S4), shared by the service worker and the
// pages. Every install talks to the same local bot address unless the owner
// changes `bridge_port`, so the default is prefilled; a value copied from a
// sentence (".../bridge." with a trailing dot) once made every offers POST
// answer 404 while pairing still worked, so whatever is saved is cleaned and
// checked first.

export const DEFAULT_BRIDGE_URL = 'http://127.0.0.1:8765/bridge';

const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]']);
const LEADING_JUNK = /^[\s"'`<(‘“]+/;
const TRAILING_JUNK = /[\s.,;)"'`>’”]+$/;

// Spaces, quotes and the punctuation a sentence leaves around a pasted URL,
// and a trailing "/" after "/bridge", are dropped. Nothing else is changed.
export function cleanBridgeUrl(raw) {
  let text = String(raw === undefined || raw === null ? '' : raw).replace(LEADING_JUNK, '');
  for (;;) {
    const next = text.replace(TRAILING_JUNK, '').replace(/\/bridge\/+$/i, '/bridge');
    if (next === text) return text;
    text = next;
  }
}

// {url, error}: `url` is the canonical `http(s)://<loopback host>[:port]/bridge`
// or '' (blank); `error` is null, 'invalid' (not an http(s) URL), 'notLoopback'
// (the bot only listens on this computer) or 'notBridge' (the path must be
// /bridge, without a query).
export function parseBridgeUrl(raw) {
  const text = cleanBridgeUrl(raw);
  if (!text) return { url: '', error: null };
  let u;
  try {
    u = new URL(text);
  } catch (_) {
    return { url: '', error: 'invalid' };
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return { url: '', error: 'invalid' };
  if (!LOOPBACK_HOSTS.has(u.hostname.toLowerCase())) return { url: '', error: 'notLoopback' };
  if (u.pathname !== '/bridge' || u.search || u.hash) return { url: '', error: 'notBridge' };
  return { url: `${u.protocol}//${u.host}/bridge`, error: null };
}

// The stored value as it should be used: the canonical form when it is a
// valid bridge URL once cleaned, otherwise the value unchanged (an invalid
// one is reported by the options page, never silently replaced).
export function healedBridgeUrl(raw) {
  const { url, error } = parseBridgeUrl(raw);
  return url && !error ? url : String(raw === undefined || raw === null ? '' : raw);
}
