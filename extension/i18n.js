// Translation lookup shared by the service worker and the pages.
// Strings live in _locales/<lang>/messages.json (English is the default
// locale). When chrome.i18n is unavailable (tests, a missing key) the
// English fallback passed by the caller is returned, or the key itself.

export function t(key, substitutions, fallback) {
  let text = '';
  try {
    text = globalThis.chrome?.i18n?.getMessage(key, substitutions) || '';
  } catch {
    text = '';
  }
  if (text && text !== key) return text;
  return fallback ?? key;
}
