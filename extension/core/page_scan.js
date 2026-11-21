// Reading one search results page in the scrape tab (spec 4.5 steps 1-5),
// shared by the round and the `test-scrape` message.

import { t } from '../i18n.js';
import { noResultsSource } from '../portals/registry.js';
import { SESSION_EXPIRED_CODE } from './constants.js';
import { sleep } from './util.js';
import { bounded } from './cancel.js';

// The portal's login wall. Its message is the code scan_report.js classifies
// as `session_expired`; `userMessage` is the text shown to the owner.
export class SessionExpiredError extends Error {
  constructor(portal) {
    super(SESSION_EXPIRED_CODE);
    this.name = 'SessionExpiredError';
    this.portal = portal;
    this.userMessage = t('sw_error_session_expired', [portal.label], `${portal.label} session expired`);
  }
}

// Page function: does the page text show the portal's own "no results" copy?
// Injected into the tab, so it must stay self-contained.
function detectNoResultsInPage(source) {
  const text = document.body ? document.body.innerText || '' : '';
  return new RegExp(source, 'i').test(text);
}

async function confirmedEmpty(tab, portal) {
  try {
    return (await tab.run(detectNoResultsInPage, [noResultsSource(portal)])) === true;
  } catch {
    return false;
  }
}

// Loads `url` and returns {cards, noResults}: the raw cards of the portal's
// extractor and, for an empty page, whether the emptiness is confirmed.
export async function scanPage(tab, url, portal) {
  await tab.load(url);
  if (typeof portal.isSessionExpired === 'function' && portal.isSessionExpired(await tab.currentUrl())) {
    throw new SessionExpiredError(portal);
  }
  await bounded(sleep(portal.settleMs || 0));
  const result = await tab.run(portal.extractCards);
  const cards = Array.isArray(result) ? result : [];
  const noResults = cards.length === 0 ? await confirmedEmpty(tab, portal) : false;
  return { cards, noResults };
}
