'use strict';

// Node harness for the scan-report wiring in extension/background.js
// (offer-gates-and-health T5b). background.js is imported as a module on top of
// stubbed chrome.* APIs, fetch and timers; a round is driven through its
// `run-now` message and the POSTs it makes are inspected. The page functions it
// injects are stubbed at chrome.scripting.executeScript (the card scrapers
// return the scenario's cards; the no-results probe runs for real against a
// fake `document`). Invoked by tests/test_extension_scan_report.py. Exit 0 on
// success.

const path = require('path');
const { pathToFileURL } = require('url');

const failures = [];

function check(name, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) failures.push(`${name}: expected ${e}, got ${a}`);
}

// background.js logs failed reports and filtered jobs; keep the harness output clean.
const log = [];
const realError = console.error;
console.error = (...args) => log.push(args.join(' '));
console.log = (...args) => log.push(args.join(' '));

const realSetTimeout = globalThis.setTimeout;
const watchdog = realSetTimeout(() => {
  realError('harness watchdog: a round did not finish');
  process.exit(2);
}, 20000);
watchdog.unref();

// --- fake timers: every delay elapses on the next macrotask, and is recorded ---
const timerDelays = new Map();
let firingTimer = null;
const pending = new Map();
let nextTimer = 1;
globalThis.setTimeout = (fn, ms) => {
  const id = nextTimer++;
  timerDelays.set(id, ms);
  pending.set(id, setImmediate(() => {
    pending.delete(id);
    firingTimer = id;
    try {
      fn();
    } finally {
      firingTimer = null;
    }
  }));
  return id;
};
globalThis.clearTimeout = (id) => {
  clearImmediate(pending.get(id));
  pending.delete(id);
};

// --- stubbed browser APIs ----------------------------------------------------
const TAB = 7;
const store = {};
const handlers = {};
const notifications = [];
let scenario = {};
const detailCalls = [];
const detailArgsSeen = [];
let lastTabUrl = '';

function clone(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

globalThis.chrome = {
  storage: {
    local: {
      async get(keys) {
        const out = {};
        for (const key of Array.isArray(keys) ? keys : [keys]) {
          if (key in store) out[key] = clone(store[key]);
        }
        return out;
      },
      async set(values) {
        Object.assign(store, clone(values));
      },
      async remove(key) {
        delete store[key];
      },
    },
  },
  tabs: {
    async create() {
      return { id: TAB };
    },
    async update(_id, props) {
      if (props && props.url) lastTabUrl = props.url;
    },
    async get() {
      return { id: TAB, url: scenario.tabUrl || 'https://www.linkedin.com/jobs/search/?keywords=ai' };
    },
    async remove() {},
    onUpdated: {
      addListener(listener) {
        listener(TAB, { status: 'complete' });
      },
      removeListener() {},
    },
  },
  scripting: {
    async executeScript({ func, args }) {
      // Any portal's card extractor (scaffolded portals are named extract<Id>Cards).
      if (func.name === 'extractJobsFromPage' || /^extract\w+Cards$/.test(func.name)) {
        if (scenario.scrapeError) throw scenario.scrapeError;
        return [{ result: scenario.cards }];
      }
      if (func.name === 'extractInfojobsDetail' || func.name === 'extractDetailText') {
        detailCalls.push(func.name);
        detailArgsSeen.push(args || []);
        if (scenario.detailError) throw scenario.detailError;
        const text = scenario.detailFor ? scenario.detailFor(lastTabUrl) : scenario.detailText;
        return [{ result: text === undefined ? 'Descripcion de la oferta '.repeat(20) : text }];
      }
      if (func.name === 'detectNoResultsInPage') {
        if (scenario.probeError) throw scenario.probeError;
        globalThis.document = { body: { innerText: scenario.pageText || '' } };
        try {
          return [{ result: func(...(args || [])) }];
        } finally {
          delete globalThis.document;
        }
      }
      throw new Error(`unexpected injected function: ${func.name}`);
    },
  },
  // Scanning needs the portal's (optional) host permission; every portal is granted here.
  permissions: {
    async contains() {
      return true;
    },
  },
  alarms: {
    async clear() {},
    async create() {},
    onAlarm: { addListener() {} },
  },
  runtime: {
    onInstalled: { addListener() {} },
    onStartup: { addListener() {} },
    onMessage: {
      addListener(listener) {
        handlers.message = listener;
      },
    },
  },
  action: {
    async setBadgeBackgroundColor() {},
    async setBadgeText() {},
  },
  notifications: {
    async create(options) {
      notifications.push(options);
    },
  },
};

const posts = [];
globalThis.fetch = async (url, init = {}) => {
  const call = {
    url,
    headers: init.headers || {},
    body: init.body ? JSON.parse(init.body) : null,
    signal: init.signal,
    // Delay of the timer whose callback aborted THIS request's signal (null: not aborted by a timer).
    abortTimerMs: null,
  };
  if (init.signal) {
    init.signal.addEventListener('abort', () => {
      call.abortTimerMs = timerDelays.has(firingTimer) ? timerDelays.get(firingTimer) : null;
    });
  }
  posts.push(call);
  if (scenario.hangReports && url.endsWith('/scan-report')) {
    await new Promise((resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(new Error('aborted')));
    });
  }
  if (url.endsWith('/bridge') && scenario.bridgeThrow) throw scenario.bridgeThrow;
  if (url.endsWith('/bridge') && scenario.bridgeStatus) {
    return { ok: false, status: scenario.bridgeStatus, text: async () => 'boom' };
  }
  return { ok: true, status: 200, text: async () => '' };
};

// --- one round per scenario ----------------------------------------------------
const BEACON = {
  id: 'b1',
  name: 'Barcelona AI',
  url: 'https://www.linkedin.com/jobs/search/?keywords=ai',
  enabled: true,
};

async function runRound(next) {
  scenario = next;
  posts.length = 0;
  notifications.length = 0;
  for (const key of Object.keys(store)) delete store[key];
  store.settings = {
    botToken: '',
    chatId: '',
    intervalMinutes: 15,
    paused: false,
    bridgeUrl: 'http://127.0.0.1:8787/bridge',
    bridgeSecret: 's3cret',
  };
  store.beacons = [BEACON];
  const response = await new Promise((resolve) => {
    handlers.message({ type: 'run-now' }, null, resolve);
  });
  const reports = posts.filter((p) => p.url.endsWith('/scan-report'));
  const bridged = posts.filter((p) => p.url.endsWith('/bridge'));
  return { response, reports, bridged };
}

const card = (id, title, company) => ({
  id,
  title,
  company,
  location: 'Barcelona',
  link: `https://www.linkedin.com/jobs/view/${id}/`,
});

async function main() {
  const modUrl = pathToFileURL(
    path.resolve(__dirname, '..', '..', 'extension', 'background.js')
  ).href;
  await import(modUrl);
  check('background registers its message handler', typeof handlers.message, 'function');

  // 1. The success report counts the RAW cards, before the title filter.
  {
    const { response, reports, bridged } = await runRound({
      cards: [
        card('1', 'AI Engineer', 'Acme'),
        card('2', 'Senior AI Engineer', ''),
        card('3', 'Marketing Manager', 'Foo'),
      ],
    });
    check('raw: round ok', response.ok, true);
    check('raw: exactly one report', reports.length, 1);
    const body = reports[0] && reports[0].body;
    check('raw: counts every parsed card', body && [body.status, body.jobs_found, body.empty_company], ['ok', 3, 1]);
    check('raw: report names the beacon', body && [body.beacon_id, body.beacon_name], ['b1', 'Barcelona AI']);
    check('raw: report goes next to the bridge', reports[0] && reports[0].url, 'http://127.0.0.1:8787/scan-report');
    check('raw: report carries the bridge secret', reports[0] && reports[0].headers['X-CVA-Bridge-Secret'], 's3cret');
    check('raw: the bridge payload IS filtered', bridged.map((b) => b.body.jobs.length), [1]);
  }

  // 2. A failure AFTER the scan was reported must not report the scan again.
  {
    const exploding = card('1', 'AI Engineer', 'Acme');
    Object.defineProperty(exploding, 'title', {
      get() {
        throw new Error('boom');
      },
    });
    const { response, reports, bridged } = await runRound({ cards: [exploding] });
    check('guard: round survives', response.ok, true);
    check('guard: a single report, the successful scan', reports.map((r) => [r.body.status, r.body.jobs_found]), [['ok', 1]]);
    check('guard: nothing was bridged', bridged.length, 0);
    check('guard: the round records the error', [store.lastCheck.b1.status, store.lastCheck.b1.error], ['error', 'boom']);
  }

  // 3. A scan that fails is reported once, with its class.
  for (const [name, next, status] of [
    ['429', { scrapeError: new Error('HTTP 429') }, 'rate_limited'],
    ['too many requests', { scrapeError: new Error('Too Many Requests') }, 'rate_limited'],
    ['generic', { scrapeError: new Error('boom') }, 'error'],
    ['session', { tabUrl: 'https://www.linkedin.com/authwall?x=1', cards: [] }, 'session_expired'],
  ]) {
    const { reports } = await runRound(next);
    check(`failure ${name}: one report`, reports.map((r) => [r.body.status, r.body.jobs_found]), [[status, 0]]);
  }

  // 4. Zero cards: only the portal's own "no results" copy is a healthy empty scan.
  for (const [name, next, status] of [
    ['marker', { cards: [], pageText: 'Jobs\nNo matching jobs found.\nTry again' }, 'ok'],
    ['marker in Spanish', { cards: [], pageText: 'No se han encontrado empleos que coincidan' }, 'ok'],
    ['blank page', { cards: [], pageText: '' }, 'empty_unconfirmed'],
    ['unrelated text', { cards: [], pageText: 'Something went wrong' }, 'empty_unconfirmed'],
    ['probe fails', { cards: [], pageText: 'No matching jobs found.', probeError: new Error('tab closed') }, 'empty_unconfirmed'],
  ]) {
    const { response, reports } = await runRound(next);
    check(`empty ${name}: round ok`, response.ok, true);
    check(`empty ${name}: one report`, reports.map((r) => [r.body.status, r.body.jobs_found]), [[status, 0]]);
    check(`empty ${name}: the round still records ok`, store.lastCheck.b1.status, 'ok');
  }

  // 5. Cards found are ok and never look for the marker.
  {
    const { reports } = await runRound({ cards: [card('1', 'AI Engineer', 'Acme')], pageText: 'No matching jobs found.' });
    check('cards win over a stray marker', reports.map((r) => r.body.status), ['ok']);
  }

  // 6. A report the bridge never answers must not stall the round.
  {
    const { response, reports, bridged } = await runRound({
      cards: [card('1', 'AI Engineer', 'Acme')],
      hangReports: true,
    });
    check('hang: the round completes', response.ok, true);
    check('hang: the report was attempted once', reports.length, 1);
    const [report] = reports;
    check('hang: the report fetch is abortable', report.signal instanceof AbortSignal, true);
    check('hang: the abort timer is bounded', report.abortTimerMs !== null && report.abortTimerMs > 0 && report.abortTimerMs <= 10000, true);
    check('hang: the abort fired', Boolean(report.signal && report.signal.aborted), true);
    check('hang: the bridge POST still went out', bridged.length, 1);
    check('hang: the round records ok', store.lastCheck.b1.status, 'ok');
  }

  // 7. No bridge configured: nothing is reported and, since public-release S2,
  // nothing is scanned: the round answers the missing-bridge state.
  {
    scenario = {};
    posts.length = 0;
    for (const key of Object.keys(store)) delete store[key];
    store.settings = { botToken: '', chatId: '', bridgeUrl: '', bridgeSecret: '' };
    store.beacons = [BEACON];
    scenario = { cards: [card('1', 'AI Engineer', 'Acme')] };
    const response = await new Promise((resolve) => {
      handlers.message({ type: 'run-now' }, null, resolve);
    });
    check('no bridge: round refused (S2)', [response.ok, response.bridgeMissing], [false, true]);
    check('no bridge: nothing posted', posts.length, 0);
  }

  // 8. Auto beacons from the `searches` setting are scanned like manual ones.
  {
    scenario = {};
    posts.length = 0;
    for (const key of Object.keys(store)) delete store[key];
    store.settings = {
      botToken: '', chatId: '', intervalMinutes: 15, paused: false,
      bridgeUrl: 'http://127.0.0.1:8787/bridge', bridgeSecret: 's3cret',
    };
    store.beacons = [];
    store.searches = [
      { id: 's1', name: 'AI BCN', keywords: 'ai engineer', location: 'Barcelona', filterProfile: 'ai', portals: ['linkedin'], titleFilter: 'on' },
      { id: 's2', name: 'Any', keywords: 'whatever', filterProfile: 'ai', portals: ['linkedin'], titleFilter: 'off' },
    ];
    scenario = {
      cards: [card('1', 'AI Engineer', 'Acme'), card('3', 'Marketing Manager', 'Foo')],
    };
    const response = await new Promise((resolve) => {
      handlers.message({ type: 'run-now' }, null, resolve);
    });
    const reports = posts.filter((p) => p.url.endsWith('/scan-report'));
    const bridged = posts.filter((p) => p.url.endsWith('/bridge'));
    check('auto: round ok', response.ok, true);
    check('auto: one health report per auto beacon', reports.map((r) => [r.body.beacon_id, r.body.beacon_name]),
      [['auto:s1:linkedin', 'AI BCN · LinkedIn'], ['auto:s2:linkedin', 'Any · LinkedIn']]);
    check('auto: title filter on drops the off-profile card', bridged[0] && bridged[0].body.jobs.map((j) => j.title), ['AI Engineer']);
    check('auto: title filter off sends every new card', bridged[1] && bridged[1].body.jobs.map((j) => j.title), ['AI Engineer', 'Marketing Manager']);
    check('auto: seen ids are kept per auto beacon id', Object.keys(store.seenJobs).sort(), ['auto:s1:linkedin', 'auto:s2:linkedin']);
    check('auto: lastCheck keyed by the auto id', Object.keys(store.lastCheck).sort(), ['auto:s1:linkedin', 'auto:s2:linkedin']);
    check('auto: auto beacons are never persisted into manual beacons', store.beacons, []);
  }

  // 9. InfoJobs auto beacon: adapter dispatch (cards, per-portal markers, detail pass,
  //    portal label, per-round detail cap).
  {
    const ijCard = (i) => ({
      id: `ij${i}`, title: `Ingeniero ${i}`, company: 'Acme', location: 'Barcelona',
      link: `https://www.infojobs.net/barcelona/ingeniero-${i}/of-ij${i}/`,
    });
    const setup = (extra = {}) => {
      posts.length = 0;
      detailCalls.length = 0;
      for (const key of Object.keys(store)) delete store[key];
      store.settings = {
        botToken: 'tok', chatId: '1', intervalMinutes: 15, paused: false,
        bridgeUrl: 'http://127.0.0.1:8787/bridge', bridgeSecret: 's3cret',
      };
      store.beacons = [];
      store.searches = [
        { id: 'j1', name: 'IA', keywords: 'ingeniero ia', location: 'Barcelona', filterProfile: 'ai', portals: ['infojobs'], titleFilter: 'off' },
      ];
      store.portalSeenRecovery = true; // models a profile already past the one-time recovery
      Object.assign(store, extra);
    };
    const go = () => new Promise((resolve) => { handlers.message({ type: 'run-now' }, null, resolve); });
    const split = () => ({
      reports: posts.filter((p) => p.url.endsWith('/scan-report')),
      bridged: posts.filter((p) => p.url.endsWith('/bridge')),
      telegram: posts.filter((p) => p.url.includes('api.telegram.org')),
    });

    // 9a. cards via the InfoJobs adapter, description attached through extractDetail
    setup();
    scenario = { cards: [ijCard(1), ijCard(2)], tabUrl: 'https://www.infojobs.net/x' };
    let response = await go();
    let { reports, bridged } = split();
    check('infojobs auto: round ok', response.ok, true);
    check('infojobs auto: report names the auto beacon', reports.map((r) => [r.body.beacon_id, r.body.beacon_name, r.body.jobs_found]),
      [['auto:j1:infojobs', 'IA · InfoJobs', 2]]);
    check('infojobs auto: one detail pass per new offer', detailCalls.length, 2);
    check('infojobs auto: bridged offers carry the detail text', bridged[0] && bridged[0].body.jobs.map((j) => (j.text || '').startsWith('Descripcion')), [true, true]);

    // 9b. per-portal no-results markers: the Spanish copy is a healthy empty scan, LinkedIn's English one is not
    for (const [name, pageText, status] of [
      ['spanish marker', 'No se han encontrado empleos para tu busqueda', 'ok'],
      ['linkedin english marker', 'No matching jobs found.', 'empty_unconfirmed'],
    ]) {
      setup();
      scenario = { cards: [], pageText, tabUrl: 'https://www.infojobs.net/x' };
      await go();
      check(`infojobs empty ${name}`, split().reports.map((r) => r.body.status), [status]);
    }

    // 9c. notifications use the portal label, not a hard-coded LinkedIn one
    setup({ seenJobs: { 'auto:j1:infojobs': ['old'] } });
    scenario = { cards: [ijCard(1)], tabUrl: 'https://www.infojobs.net/x' };
    await go();
    // extension-rewrite S4: the extension never sends Telegram alerts itself.
    check('label: no telegram alert', split().telegram.length, 0);
    setup({ seenJobs: { 'auto:l1:linkedin': ['old'] } });
    store.searches = [{ id: 'l1', name: 'L', keywords: 'ai', filterProfile: 'ai', portals: ['linkedin'], titleFilter: 'off' }];
    scenario = { cards: [card('9', 'AI Engineer', 'Acme')] };
    await go();
    check('label: still no telegram alert', split().telegram.length, 0);

    // 9d. detail passes are capped per beacon per round; the rest wait, unseen, for the next round
    setup();
    scenario = { cards: Array.from({ length: 20 }, (_, i) => ijCard(i + 1)), tabUrl: 'https://www.infojobs.net/x' };
    await go();
    ({ bridged } = split());
    check('cap: at most 15 detail passes in a round', detailCalls.length, 15);
    check('cap: only the enriched offers are bridged', bridged.map((b) => b.body.jobs.length), [15]);
    check('cap: deferred offers are not marked seen', store.seenJobs['auto:j1:infojobs'].length, 15);
    check('cap: deferred offers are the tail', store.seenJobs['auto:j1:infojobs'].includes('ij16'), false);
    posts.length = 0;
    detailCalls.length = 0;
    await go();
    ({ bridged } = split());
    check('cap: the next round picks up the remaining 5', [detailCalls.length, bridged.map((b) => b.body.jobs.map((j) => j.url.match(/of-(ij\d+)/)[1]))],
      [5, [['ij16', 'ij17', 'ij18', 'ij19', 'ij20']]]);
    check('cap: everything seen afterwards', store.seenJobs['auto:j1:infojobs'].length, 20);
  }

  // 10. Delivery correctness (portal-detail-text D2): a text-less job of a detail
  //     portal is not sent and not marked seen (bounded retries); a job is marked
  //     seen only after the bridge accepted it; raw alerts stay once-only.
  {
    const ijCard = (i) => ({
      id: `ij${i}`, title: `Ingeniero ${i}`, company: 'Acme', location: 'Barcelona',
      link: `https://www.infojobs.net/barcelona/ingeniero-${i}/of-ij${i}/`,
    });
    const KEY = 'auto:j1:infojobs';
    const setup = (extra = {}, portal = 'infojobs') => {
      posts.length = 0;
      detailCalls.length = 0;
      detailArgsSeen.length = 0;
      for (const key of Object.keys(store)) delete store[key];
      store.settings = {
        botToken: 'tok', chatId: '1', intervalMinutes: 15, paused: false,
        bridgeUrl: 'http://127.0.0.1:8787/bridge', bridgeSecret: 's3cret',
      };
      store.beacons = [];
      store.searches = [
        { id: 'j1', name: 'IA', keywords: 'ingeniero ia', location: 'Barcelona', filterProfile: 'ai', portals: [portal], titleFilter: 'off' },
      ];
      store.seenJobs = { [`auto:j1:${portal}`]: ['old'] };
      store.portalSeenRecovery = true; // models a profile already past the one-time recovery
      Object.assign(store, extra);
    };
    const go = () => new Promise((resolve) => { handlers.message({ type: 'run-now' }, null, resolve); });
    const split = () => ({
      bridged: posts.filter((p) => p.url.endsWith('/bridge')),
      telegram: posts.filter((p) => p.url.includes('api.telegram.org')),
    });
    const ids = (b) => b.body.jobs.map((j) => j.url.match(/of-(ij\d+)/)[1]);
    const noText = (url) => (url.includes('of-ij1/') ? null : undefined);

    // 10a. a text-less job is neither sent, alerted nor marked seen; its attempt is counted
    setup();
    scenario = { cards: [ijCard(1), ijCard(2)], tabUrl: 'https://www.infojobs.net/x', detailFor: noText };
    await go();
    let { bridged, telegram } = split();
    check('retry: only the job with text is bridged', bridged.map(ids), [['ij2']]);
    check('retry: no telegram alert (S4)', telegram.length, 0);
    check('retry: the text-less job stays unseen', store.seenJobs[KEY].sort(), ['ij2', 'old']);
    check('retry: its attempt is counted', (store.detailAttempts || {})[KEY], { ij1: 1 });

    // 10b. a failing detail load (exception) counts as an attempt too; after 3 rounds the job is given up
    setup();
    scenario = { cards: [ijCard(1)], tabUrl: 'https://www.infojobs.net/x', detailError: new Error('tab closed') };
    await go();
    check('retry: a detail exception is an attempt', (store.detailAttempts || {})[KEY], { ij1: 1 });
    await go();
    check('retry: second attempt counted', (store.detailAttempts || {})[KEY], { ij1: 2 });
    check('retry: still unseen after two rounds', store.seenJobs[KEY], ['old']);
    await go();
    ({ bridged, telegram } = split());
    check('give up: the third failure marks it seen', store.seenJobs[KEY].sort(), ['ij1', 'old']);
    check('give up: its counter is cleared', Object.keys(store.detailAttempts || {}).includes(KEY), false);
    check('give up: never bridged nor alerted', [bridged.length, telegram.length], [0, 0]);

    // 10c. a bridge failure (500 or network error) keeps the jobs unseen and unalerted; the retry alerts once
    for (const [name, fail] of [['http 500', { bridgeStatus: 500 }], ['network error', { bridgeThrow: new Error('offline') }]]) {
      setup();
      scenario = { cards: [ijCard(1), ijCard(2)], tabUrl: 'https://www.infojobs.net/x', ...fail };
      await go();
      ({ bridged, telegram } = split());
      check(`bridge ${name}: attempted once`, bridged.length, 1);
      check(`bridge ${name}: nothing alerted`, telegram.length, 0);
      check(`bridge ${name}: jobs stay unseen`, store.seenJobs[KEY], ['old']);
      check(`bridge ${name}: no attempts burnt`, Object.keys(store.detailAttempts || {}).includes(KEY), false);
      scenario = { cards: [ijCard(1), ijCard(2)], tabUrl: 'https://www.infojobs.net/x' };
      await go();
      ({ bridged, telegram } = split());
      check(`bridge ${name}: retried next round`, bridged.map(ids).slice(-1), [['ij1', 'ij2']]);
      check(`bridge ${name}: still no telegram alert (S4)`, telegram.length, 0);
      check(`bridge ${name}: seen after the 2xx`, store.seenJobs[KEY].sort(), ['ij1', 'ij2', 'old']);
    }

    // 10f. a deterministic bridge rejection (HTTP 4xx) burns one attempt per job; at 3 the job is
    //      given up (marked seen) and gets the raw Telegram fallback alert once. A bridge outage
    //      (5xx / network) never burns attempts.
    {
      setup();
      scenario = { cards: [ijCard(1), ijCard(2)], tabUrl: 'https://www.infojobs.net/x', bridgeStatus: 401 };
      await go();
      check('reject 4xx: attempts counted per job', (store.detailAttempts || {})[KEY], { ij1: 1, ij2: 1 });
      check('reject 4xx: jobs stay unseen', store.seenJobs[KEY], ['old']);
      await go();
      check('reject 4xx: second attempt counted', (store.detailAttempts || {})[KEY], { ij1: 2, ij2: 2 });
      check('reject 4xx: still nothing alerted', split().telegram.length, 0);
      await go();
      ({ bridged, telegram } = split());
      check('reject 4xx: given up after 3 attempts (marked seen)', store.seenJobs[KEY].sort(), ['ij1', 'ij2', 'old']);
      check('reject 4xx: counters cleared', Object.keys(store.detailAttempts || {}).includes(KEY), false);
      check('reject 4xx: no Telegram fallback (S4)', telegram.length, 0);
      await go();
      check('reject 4xx: no further bridge calls once given up', [split().bridged.length, split().telegram.length], [3, 0]);

      // a 4xx round that recovers: the counter is cleared by the accepted retry
      setup();
      scenario = { cards: [ijCard(1)], tabUrl: 'https://www.infojobs.net/x', bridgeStatus: 400 };
      await go();
      scenario = { cards: [ijCard(1)], tabUrl: 'https://www.infojobs.net/x' };
      await go();
      check('reject 4xx then 2xx: seen, counter cleared, alerted once',
        [store.seenJobs[KEY].sort(), Object.keys(store.detailAttempts || {}).includes(KEY), split().telegram.length], [['ij1', 'old'], false, 0]);

      // no Telegram configured: the give-up is silent
      setup();
      store.settings.botToken = '';
      scenario = { cards: [ijCard(1)], tabUrl: 'https://www.infojobs.net/x', bridgeStatus: 413 };
      await go(); await go(); await go();
      check('reject 4xx without Telegram: given up, no alert', [store.seenJobs[KEY].sort(), split().telegram.length], [['ij1', 'old'], 0]);

      // an outage never gives up, however many rounds
      setup();
      scenario = { cards: [ijCard(1)], tabUrl: 'https://www.infojobs.net/x', bridgeStatus: 503 };
      await go(); await go(); await go(); await go();
      check('outage 5xx: never gives up nor alerts', [store.seenJobs[KEY], split().telegram.length, Object.keys(store.detailAttempts || {}).includes(KEY)], [['old'], 0, false]);
    }

    // 10d. no bridge configured: nothing sent and, since public-release S2,
    // nothing scanned, so the offer is not marked seen
    setup();
    store.settings.bridgeUrl = '';
    scenario = { cards: [ijCard(1)], tabUrl: 'https://www.infojobs.net/x' };
    await go();
    ({ bridged, telegram } = split());
    check('no bridge: nothing sent (S4) and job not seen (S2)', [bridged.length, telegram.length, store.seenJobs[KEY].sort()], [0, 0, ['old']]);

    // 10e. other portals inject the shared extractor with their selector
    setup({}, 'indeed');
    scenario = {
      cards: [{ id: '0123456789abcdef', title: 'Ingeniero', company: 'Acme', location: 'Barcelona', link: 'https://es.indeed.com/viewjob?jk=0123456789abcdef' }],
      tabUrl: 'https://es.indeed.com/x',
    };
    await go();
    check('indeed: detail extractor injected with its selector', detailArgsSeen, [['#jobDescriptionText']]);
    check('indeed: bridged with the text', split().bridged.map((b) => b.body.jobs.map((j) => (j.text || '').length > 0)), [[true]]);
  }

  // 11. Recovery (portal-detail-text D3): once, the seen state of the auto beacons of
  //     portals other than LinkedIn is cleared (those offers never reached the bot);
  //     the recovery pass stays silent on Telegram until the backlog is drained.
  {
    const ijCard = (i) => ({
      id: `ij${i}`, title: `Ingeniero ${i}`, company: 'Acme', location: 'Barcelona',
      link: `https://www.infojobs.net/barcelona/ingeniero-${i}/of-ij${i}/`,
    });
    const reset = (searches, seenJobs, extra = {}) => {
      posts.length = 0;
      for (const key of Object.keys(store)) delete store[key];
      store.settings = {
        botToken: 'tok', chatId: '1', intervalMinutes: 15, paused: false,
        bridgeUrl: 'http://127.0.0.1:8787/bridge', bridgeSecret: 's3cret',
      };
      store.beacons = [];
      store.searches = searches;
      store.seenJobs = seenJobs;
      Object.assign(store, extra);
    };
    const go = () => new Promise((resolve) => { handlers.message({ type: 'run-now' }, null, resolve); });
    const liSearch = { id: 'l1', name: 'L', keywords: 'ai', filterProfile: 'ai', portals: ['linkedin'], titleFilter: 'off' };
    const ijSearch = { id: 'j1', name: 'IA', keywords: 'ingeniero ia', location: 'Barcelona', filterProfile: 'ai', portals: ['infojobs'], titleFilter: 'off' };

    // 11a. one-time clearing: auto beacons of non-LinkedIn portals only, with their attempt counters
    reset([liSearch], {
      'auto:j1:infojobs': ['a'], 'auto:i1:indeed': ['b'], 'auto:l1:linkedin': ['x'], manual1: ['m'],
    }, { detailAttempts: { 'auto:i1:indeed': { b: 1 }, 'auto:l1:linkedin': { x: 1 } } });
    scenario = { cards: [card('9', 'AI Engineer', 'Acme')] };
    await go();
    check('recovery: non-LinkedIn auto seen state cleared', Object.keys(store.seenJobs).sort(), ['auto:l1:linkedin', 'manual1']);
    check('recovery: LinkedIn and manual seen state untouched', [store.seenJobs.manual1, store.seenJobs['auto:l1:linkedin'].includes('x')], [['m'], true]);
    check('recovery: attempt counters follow', Object.keys(store.detailAttempts), ['auto:l1:linkedin']);
    check('recovery: the flag is persisted', store.portalSeenRecovery, true);
    check('recovery: cleared beacons are marked recovering', (store.recoveringBeacons || []).slice().sort(), ['auto:i1:indeed', 'auto:j1:infojobs']);

    // 11b. runs once only
    store.seenJobs['auto:j1:infojobs'] = ['again'];
    await go();
    check('recovery: a second round does not clear again', store.seenJobs['auto:j1:infojobs'], ['again']);

    // 11c. the recovery pass stays silent until the backlog drains, then alerts resume
    reset([ijSearch], { 'auto:j1:infojobs': ['old'], 'auto:l1:linkedin': ['x'] }, {});
    scenario = { cards: Array.from({ length: 20 }, (_, i) => ijCard(i + 1)), tabUrl: 'https://www.infojobs.net/x' };
    await go();
    const tg = () => posts.filter((p) => p.url.includes('api.telegram.org')).length;
    const br = () => posts.filter((p) => p.url.endsWith('/bridge')).map((p) => p.body.jobs.length);
    check('recovery pass 1: 15 offers bridged, no raw alert', [br(), tg()], [[15], 0]);
    check('recovery pass 1: still recovering (5 deferred)', store.recoveringBeacons, ['auto:j1:infojobs']);
    posts.length = 0;
    await go();
    check('recovery pass 2: the other 5 bridged, still no raw alert', [br(), tg()], [[5], 0]);
    check('recovery pass 2: backlog drained, recovery over', store.recoveringBeacons, []);
    posts.length = 0;
    scenario = { cards: Array.from({ length: 21 }, (_, i) => ijCard(i + 1)), tabUrl: 'https://www.infojobs.net/x' };
    await go();
    check('after recovery: a genuinely new offer is bridged again, no telegram (S4)', [br(), tg()], [[1], 0]);
  }

  if (failures.length) {
    realError(`FAIL: ${failures.length} assertion(s) failed:`);
    for (const f of failures) realError(' - ' + f);
    process.exitCode = 1;
  } else {
    process.stdout.write('OK: all extension/background.js scan-report assertions passed\n');
  }
}

main().catch((e) => {
  realError('harness crashed:', (e && e.stack) || e);
  process.exitCode = 1;
});
