// What happens to the cards of one scanned page (spec 2.4, 4.7-4.9): title
// filter, detail pass, bridge POST, attempt counters and the seen list.
//
// An offer is marked seen once it is settled: sent and accepted, filtered
// out or given up after MAX_DETAIL_ATTEMPTS. Offers that must be retried
// (deferred by the detail cap, detail text unreadable, bridge outage or
// rejection) stay unseen. A round never starts without a configured bridge
// (round.js), so the no-bridge branch of sendOffers is only a guard.

import { filterForBeacon, passesFilter } from '../filter.js';
import { bridgeConfigured } from './storage.js';
import {
  postOffers, BRIDGE_ACCEPTED, BRIDGE_OUTAGE, BRIDGE_REJECTED,
} from './bot_bridge.js';
import {
  MAX_DETAIL_PER_ROUND, MAX_DETAIL_ATTEMPTS, SEEN_LIMIT_PER_BEACON, DETAIL_SETTLE_MS, BEFORE_DETAIL_MS,
} from './constants.js';
import { sleep, randomBetween, log } from './util.js';

function titleFilterPasses(beacon, filters) {
  if (beacon.auto && beacon.titleFilter === 'off') return () => true;
  const filter = filterForBeacon(filters, beacon);
  return (card) => passesFilter(card, filter);
}

async function readDetail(tab, portal, card) {
  try {
    await sleep(randomBetween(BEFORE_DETAIL_MS));
    await tab.load(card.link);
    await sleep(DETAIL_SETTLE_MS);
    const text = await tab.run(portal.extractDetail, portal.detailArgs || []);
    return typeof text === 'string' && text.trim() ? text : null;
  } catch {
    return null;
  }
}

// Mutable bookkeeping of one beacon during its delivery.
class Ledger {
  constructor(attempts) {
    this.attempts = { ...attempts };
    this.retry = new Set();
    this.usedCounters = false;
  }

  settle(id) {
    delete this.attempts[id];
  }

  // One more failure for the offer; at the limit it is given up (seen, never
  // sent), otherwise it waits for a later round.
  fail(id, beacon) {
    this.usedCounters = true;
    const failures = (this.attempts[id] || 0) + 1;
    if (failures >= MAX_DETAIL_ATTEMPTS) {
      delete this.attempts[id];
      log(`gave up offer ${id} of "${beacon.name}" after ${failures} attempts`);
    } else {
      this.attempts[id] = failures;
      this.retry.add(id);
    }
  }
}

async function collectOffers({ tab, portal, beacon, passing, ledger }) {
  if (typeof portal.extractDetail !== 'function') {
    return passing.map((card) => ({ card, text: null }));
  }
  ledger.usedCounters = true;
  const offers = [];
  passing.slice(MAX_DETAIL_PER_ROUND).forEach((card) => ledger.retry.add(String(card.id)));
  for (const card of passing.slice(0, MAX_DETAIL_PER_ROUND)) {
    const text = await readDetail(tab, portal, card);
    if (text) offers.push({ card, text });
    else ledger.fail(String(card.id), beacon);
  }
  return offers;
}

async function sendOffers({ settings, beacon, offers, ledger }) {
  if (!offers.length || !bridgeConfigured(settings)) {
    offers.forEach(({ card }) => ledger.settle(String(card.id)));
    return;
  }
  const outcome = await postOffers(settings, beacon, offers);
  for (const { card } of offers) {
    const id = String(card.id);
    if (outcome === BRIDGE_ACCEPTED) ledger.settle(id);
    else if (outcome === BRIDGE_REJECTED) ledger.fail(id, beacon);
    else if (outcome === BRIDGE_OUTAGE) ledger.retry.add(id);
  }
  if (outcome !== BRIDGE_ACCEPTED) ledger.usedCounters = true;
}

function updatedSeen(seenList, cards, retry) {
  const seen = new Set(seenList);
  const next = [...seenList];
  for (const card of cards) {
    const id = String(card.id);
    if (seen.has(id) || retry.has(id)) continue;
    seen.add(id);
    next.push(id);
  }
  return next.slice(-SEEN_LIMIT_PER_BEACON);
}

// Counters of offers that left the results page are dropped, but only when
// the counters were in play for this beacon this round.
function prunedAttempts(ledger, cards) {
  if (!ledger.usedCounters) return ledger.attempts;
  const onPage = new Set(cards.map((card) => String(card.id)));
  return Object.fromEntries(Object.entries(ledger.attempts).filter(([id]) => onPage.has(id)));
}

// Returns the counts for `lastCheck` and the beacon's next seen list and
// attempt counters; `drained` says nothing was left to retry.
export async function deliver({ tab, portal, beacon, cards, settings, filters, seenList, attempts }) {
  const known = new Set(seenList);
  const fresh = cards.filter((card) => !known.has(String(card.id)));
  const passes = titleFilterPasses(beacon, filters);
  const passing = fresh.filter(passes);
  const filteredOut = fresh.length - passing.length;
  if (filteredOut) log(`"${beacon.name}": ${filteredOut} new offer(s) dropped by the title filter`);

  const ledger = new Ledger(attempts);
  const offers = await collectOffers({ tab, portal, beacon, passing, ledger });
  await sendOffers({ settings, beacon, offers, ledger });

  return {
    newJobs: fresh.length,
    filteredOut,
    seenList: updatedSeen(seenList, cards, ledger.retry),
    attempts: prunedAttempts(ledger, cards),
    drained: ledger.retry.size === 0,
  };
}
