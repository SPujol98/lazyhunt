// Bounded and cancellable waits for the scan round (beta3-ux: a "Check now"
// must never spin for minutes, and Pause must stop a round promptly).
//
// A round opens a token (beginRound); `cancelRound()` (Pause) marks it and
// rejects every wait in flight with RoundCancelled. `bounded()` races a
// promise against that and an optional timeout; outside a round (a test
// scrape) only the timeout applies.

export class RoundCancelled extends Error {
  constructor() {
    super('round cancelled');
    this.name = 'RoundCancelled';
  }
}

let token = null;

export function beginRound() {
  token = { cancelled: false, waiters: new Set() };
  return token;
}

export function endRound() {
  token = null;
}

// True when a running round was asked to stop.
export function cancelRound() {
  if (!token || token.cancelled) return false;
  token.cancelled = true;
  for (const wake of token.waiters) wake();
  token.waiters.clear();
  return true;
}

export function isCancelled() {
  return Boolean(token && token.cancelled);
}

export function throwIfCancelled() {
  if (isCancelled()) throw new RoundCancelled();
}

// `promise`, unless the round is cancelled first (RoundCancelled) or `ms`
// elapses first (an Error with `message` and code `timeout`). `ms` 0 = no timeout;
// `cancellable` false = only the timeout (cleanup steps such as closing the
// tab must still run after a cancel).
export function bounded(promise, { ms = 0, message = '', cancellable = true } = {}) {
  const current = cancellable ? token : null;
  let timer = null;
  let wake = null;
  const stop = new Promise((_resolve, reject) => {
    if (ms > 0) {
      timer = setTimeout(() => {
        const error = new Error(message || `timed out after ${ms / 1000} seconds`);
        error.code = 'timeout'; // the scan report's reason (scan_report.js)
        reject(error);
      }, ms);
    }
    if (current) {
      wake = () => reject(new RoundCancelled());
      if (current.cancelled) wake();
      else current.waiters.add(wake);
    }
  });
  stop.catch(() => {});
  return Promise.race([Promise.resolve(promise), stop]).finally(() => {
    if (timer !== null) clearTimeout(timer);
    if (current && wake) current.waiters.delete(wake);
  });
}
