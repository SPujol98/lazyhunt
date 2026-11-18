// Per-portal opt-in helpers (safe-defaults T2). Pure, no chrome.*.
//
// A portal is scanned only after the owner enables it in the options page: the
// options show `termsWarning(portal)` (locale key `termsMessageKey(portal)`) and ask for confirmation, then request the
// browser permission for `portalOrigin(portal)` (an optional host permission).

// Host match pattern of a portal, derived from the adapter's own `linkHost`, so
// it cannot drift from the registry (a test pins it to manifest.json).
export function portalOrigin(portal) {
  return `https://${portal.linkHost}/*`;
}

// Portals whose terms forbid automated access get a strong warning instead of
// the generic one: the locale key the options page asks for, and the English
// text (the `en` catalogue repeats it verbatim; a test pins both).
const STRONG_TERMS = {
  linkedin: {
    key: 'termsLinkedin',
    text:
      'LinkedIn warning: the LinkedIn User Agreement forbids browser extensions that scrape or '
      + 'automate the site. Enabling LinkedIn scans runs searches in your own logged-in session, '
      + 'and your account may be restricted or suspended for it. Do it at your own risk. '
      + 'Enable LinkedIn?',
  },
};

// Locale key of a portal's terms warning: its strong one, else `termsPortal`
// (which takes the portal label as $1).
export function termsMessageKey(portal) {
  const strong = Object.prototype.hasOwnProperty.call(STRONG_TERMS, portal.id) && STRONG_TERMS[portal.id];
  return strong ? strong.key : 'termsPortal';
}

export function termsWarning(portal) {
  const label = portal.label || portal.id;
  if (Object.prototype.hasOwnProperty.call(STRONG_TERMS, portal.id)) {
    return STRONG_TERMS[portal.id].text;
  }
  return (
    `Enabling ${label}: the extension will open ${label} search pages in your browser on every `
    + `round and read the results. Check that ${label}'s terms of use allow automated access `
    + 'before turning it on; you are responsible for complying with them. '
    + `Enable ${label}?`
  );
}
