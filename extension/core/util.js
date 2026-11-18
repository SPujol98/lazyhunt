// Small helpers without chrome.* dependencies.

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function randomBetween([min, max]) {
  return min + Math.random() * (max - min);
}

export function errorMessage(error) {
  if (error && typeof error.message === 'string' && error.message) return error.message;
  return String(error);
}

export function log(...parts) {
  console.log('[lazyhunt]', ...parts);
}

export function warn(...parts) {
  console.warn('[lazyhunt]', ...parts);
}
