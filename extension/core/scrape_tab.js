// The hidden background tab the scans run in (spec 4.4, 2.10, D3).
//
// A round's tab is remembered under `scrapeTabId` so a tab left behind by a
// service worker that was stopped mid-round is reused (and then closed) by the
// next round. A test scrape uses its own unremembered tab, so it never touches
// the round's one.

import { t } from '../i18n.js';
import { PAGE_LOAD_TIMEOUT_MS, SCRIPT_TIMEOUT_MS, TAB_CALL_TIMEOUT_MS } from './constants.js';
import { bounded } from './cancel.js';
import { readKeys, writeKeys, removeKeys } from './storage.js';

async function liveTab(id) {
  if (typeof id !== 'number') return null;
  try {
    return await chrome.tabs.get(id);
  } catch {
    return null;
  }
}

// Navigates the tab and resolves on its next `complete` load, or fails after
// the load timeout. The listener is attached before navigating so a fast load
// cannot be missed.
async function navigate(tabId, url) {
  let listener;
  let timer;
  const loaded = new Promise((resolve, reject) => {
    listener = (id, info, tab) => {
      if (id !== tabId || !info || info.status !== 'complete') return;
      if (tab && tab.url === 'about:blank') return;
      resolve();
    };
    timer = setTimeout(() => {
      const error = new Error(t(
        'sw_error_load_timeout',
        [String(PAGE_LOAD_TIMEOUT_MS / 1000)],
        `The page did not finish loading within ${PAGE_LOAD_TIMEOUT_MS / 1000} seconds`,
      ));
      error.code = 'timeout'; // the message is localized: the report reads the code
      reject(error);
    }, PAGE_LOAD_TIMEOUT_MS);
    chrome.tabs.onUpdated.addListener(listener);
  });
  loaded.catch(() => {});
  try {
    await bounded(chrome.tabs.update(tabId, { url }), { ms: TAB_CALL_TIMEOUT_MS });
    await bounded(loaded); // its own load timeout; a Pause stops it at once
  } finally {
    clearTimeout(timer);
    chrome.tabs.onUpdated.removeListener(listener);
  }
}

export class ScrapeTab {
  constructor({ remembered = true } = {}) {
    this.remembered = remembered;
    this.id = null;
  }

  async ensure() {
    if (this.id !== null) return this.id;
    if (this.remembered) {
      const { scrapeTabId } = await readKeys(['scrapeTabId']);
      if (await liveTab(scrapeTabId)) {
        this.id = scrapeTabId;
        return this.id;
      }
    }
    const tab = await bounded(chrome.tabs.create({ url: 'about:blank', active: false }), { ms: TAB_CALL_TIMEOUT_MS });
    this.id = tab.id;
    if (this.remembered) await writeKeys({ scrapeTabId: this.id });
    return this.id;
  }

  async load(url) {
    await navigate(await this.ensure(), url);
  }

  async currentUrl() {
    const tab = await liveTab(this.id);
    return (tab && tab.url) || '';
  }

  // Runs a self-contained page function in the tab and returns its result.
  async run(func, args = []) {
    // A frozen or discarded tab can leave executeScript pending forever.
    const results = await bounded(chrome.scripting.executeScript({
      target: { tabId: await this.ensure() },
      func,
      args,
    }), {
      ms: SCRIPT_TIMEOUT_MS,
      message: t(
        'sw_error_script_timeout',
        [String(SCRIPT_TIMEOUT_MS / 1000)],
        `The page did not answer within ${SCRIPT_TIMEOUT_MS / 1000} seconds`,
      ),
    });
    return results && results[0] ? results[0].result : undefined;
  }

  async dispose() {
    if (this.id !== null) {
      try {
        await bounded(chrome.tabs.remove(this.id), { ms: TAB_CALL_TIMEOUT_MS, cancellable: false });
      } catch {
        // already closed by the user or the browser
      }
    }
    this.id = null;
    if (this.remembered) await removeKeys(['scrapeTabId']);
  }
}
