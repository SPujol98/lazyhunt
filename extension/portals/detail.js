// Shared per-offer detail extractor for the portals without a dedicated one
// (Indeed, Tecnoempleo, JobFluent, Manfred). Injected with
// chrome.scripting.executeScript, so it is serialized: it must stay
// self-contained (no imports, no module-scope bindings) and receives the
// portal's own description selector (or null) and an optional strictPanel flag; adapters
// expose it as `extractDetail` and the selector as `detailArgs`.
//
// Order: the portal's selector, then the largest of main/article/description-
// like containers, then the whole body. The text is trimmed, blank runs are
// collapsed and it is capped below the bridge's MAX_INLINE_TEXT_CHARS (20000).
// Returns null when too short to be an offer or when it looks like a bot
// challenge, so such pages are never sent to the bot as if they were offers.
export function extractDetailText(preferredSelector, strictPanel = false) {
  const MIN_CHARS = 200;
  const MAX_CHARS = 19000;
  // Specific bot-challenge markers only (Cloudflare and similar): generic words
  // such as "un momento" or "captcha" appear in genuine offers.
  const CHALLENGE_TEXT = /^\s*just a moment\.{0,3}\s*$|checking your browser|verify (?:you are|you're) (?:a )?human|verifica que eres (?:un )?humano|enable javascript and cookies to continue|performance (?:and|&) security by/im;
  const CHALLENGE_TITLE = /^\s*(?:just a moment|attention required|un momento|security check)/i;
  const CHALLENGE_ELEMENT = '#challenge-form, #challenge-running, .cf-challenge, [class*="challenge-platform"], script[src*="challenge-platform"]';
  const clean = (raw) => String(raw || '')
    .split('\n')
    .map((line) => line.replace(/[ \t ]+/g, ' ').trim())
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  let text = '';
  if (preferredSelector) {
    const el = document.querySelector(preferredSelector);
    if (el) {
      text = clean(el.innerText);
      // strictPanel (opt-in, e.g. foorilla's htmx panel): a matched but still
      // empty panel means "not loaded yet" -> null (retry), never the chrome.
      if (strictPanel && text.length < MIN_CHARS) return null;
    }
  }
  if (text.length < MIN_CHARS) {
    text = '';
    const containers = [
      'main', 'article', '[class*="description" i]', '[class*="descripcion" i]', '[id*="description" i]',
    ];
    for (const selector of containers) {
      document.querySelectorAll(selector).forEach((el) => {
        const candidate = clean(el.innerText);
        if (candidate.length > text.length) text = candidate;
      });
    }
  }
  if (text.length < MIN_CHARS) text = clean(document.body ? document.body.innerText : '');
  if (text.length < MIN_CHARS) return null;
  if (text.length < 1500) {
    let challengeElement = false;
    try { challengeElement = Boolean(document.querySelector(CHALLENGE_ELEMENT)); } catch (e) { /* unsupported selector */ }
    if (challengeElement || CHALLENGE_TITLE.test(document.title || '') || CHALLENGE_TEXT.test(text.slice(0, 600))) return null;
  }
  return text.slice(0, MAX_CHARS).trim();
}
