// The periodic round alarm (spec 4.1).

import { ALARM_NAME } from './constants.js';
import { readSettings, intervalMinutes } from './storage.js';

export async function reschedule() {
  const settings = await readSettings();
  await chrome.alarms.clear(ALARM_NAME);
  if (settings.paused) return;
  await chrome.alarms.create(ALARM_NAME, {
    delayInMinutes: 0.5,
    periodInMinutes: intervalMinutes(settings.intervalMinutes),
  });
}
