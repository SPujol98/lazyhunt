// Translation lookup shared by the service worker and the pages.
// Strings live in _locales/<lang>/messages.json (English is the default
// locale). By default the browser language decides (chrome.i18n). The
// `uiLanguage` setting ("auto" | "en" | "es" in chrome.storage.local) can
// force one catalogue: it is loaded with fetch and resolved here with the
// same placeholder rules as chrome.i18n.getMessage. When neither source has
// the key (tests, a missing key) the English fallback passed by the caller is
// returned, or the key itself.

export const UI_LANGUAGE_KEY = 'uiLanguage';
export const UI_LANGUAGES = ['auto', 'en', 'es'];

const catalogues = new Map();
let forced = null;

export function normalizeUiLanguage(value) {
  return UI_LANGUAGES.includes(value) ? value : 'auto';
}

// chrome.i18n.getMessage rules over one messages.json entry: $name$ becomes
// the placeholder's content, whose $1..$9 are the substitutions (a missing
// one is empty), and $$ is a literal dollar.
function resolve(entry, substitutions) {
  if (!entry || typeof entry.message !== 'string') return '';
  const list = substitutions === undefined || substitutions === null ? [] : [].concat(substitutions).map(String);
  const placeholders = entry.placeholders || {};
  return entry.message
    .replace(/\$([A-Za-z0-9_@]+)\$/g, (_m, name) => {
      const found = placeholders[name.toLowerCase()] || placeholders[name];
      if (!found || typeof found.content !== 'string') return '';
      return found.content.replace(/\$(\d)/g, (_x, n) => list[Number(n) - 1] || '');
    })
    .replace(/\$\$/g, '$');
}

async function loadCatalogue(lang) {
  if (catalogues.has(lang)) return catalogues.get(lang);
  let messages = null;
  try {
    const response = await fetch(chrome.runtime.getURL(`_locales/${lang}/messages.json`));
    const data = await response.json();
    if (data && typeof data === 'object') messages = data;
  } catch {
    messages = null;
  }
  if (messages) catalogues.set(lang, messages);
  return messages;
}

// Makes `value` the language t() uses; returns the normalized setting.
// "auto" (or a catalogue that cannot be loaded) keeps chrome.i18n.
export async function applyUiLanguage(value) {
  const lang = normalizeUiLanguage(value);
  forced = lang === 'auto' ? null : await loadCatalogue(lang);
  return lang;
}

// Reads the stored setting and applies it.
export async function loadUiLanguage() {
  let value;
  try {
    value = (await chrome.storage.local.get([UI_LANGUAGE_KEY]))[UI_LANGUAGE_KEY];
  } catch {
    value = undefined;
  }
  return applyUiLanguage(value);
}

export function t(key, substitutions, fallback) {
  let text = '';
  if (forced) text = resolve(forced[key], substitutions);
  if (text && text !== key) return text;
  try {
    text = globalThis.chrome?.i18n?.getMessage(key, substitutions) || '';
  } catch {
    text = '';
  }
  if (text && text !== key) return text;
  return fallback ?? key;
}
