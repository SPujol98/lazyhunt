// Service worker entry (manifest `background.service_worker`, an ES module).
// It only wires Chrome events to the modules under core/; the behaviour is
// specified in docs/extension-spec.md (sections 2-4 and 11).

import { ALARM_NAME } from './core/constants.js';
import { migrateStorage } from './core/migrations.js';
import { reschedule } from './core/schedule.js';
import { runRound } from './core/round.js';
import { handleMessage } from './core/messages.js';
import { warn } from './core/util.js';

async function prepare(installed) {
  try {
    await migrateStorage({ installed });
  } catch (error) {
    warn('storage migration failed:', String(error));
  }
  await reschedule();
}

chrome.runtime.onInstalled.addListener(() => prepare(true));
chrome.runtime.onStartup.addListener(() => prepare(false));

chrome.alarms.onAlarm.addListener((alarm) => {
  if (!alarm || alarm.name !== ALARM_NAME) return undefined;
  return runRound().catch((error) => warn('alarm round failed:', String(error)));
});

// Answers asynchronously: returning true keeps the channel open.
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  handleMessage(message).then(sendResponse);
  return true;
});
