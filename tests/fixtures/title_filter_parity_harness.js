'use strict';

// Node harness for the rejudge-backlog title-filter PARITY test
// (tests/test_title_filter.py). Loads extension/filter.js's own
// passesFilter()/DEFAULT_TITLE_FILTER and evaluates a JSON array of
// {title, company} jobs (passed as argv[2]) against DEFAULT_TITLE_FILTER,
// printing a JSON array of booleans to stdout — the Python test then
// compares this against cva/title_filter.py's own passes_filter() over the
// SAME inputs to prove the two ports agree.

const path = require('path');
const { pathToFileURL } = require('url');

async function main() {
  const modUrl = pathToFileURL(
    path.resolve(__dirname, '..', '..', 'extension', 'filter.js')
  ).href;
  const { passesFilter, DEFAULT_TITLE_FILTER, DEFAULT_TITLE_FILTERS } = await import(modUrl);
  if (process.argv[2] === '--dump') {
    // Both built-in profiles verbatim, for the Python constants' equality test.
    process.stdout.write(JSON.stringify(DEFAULT_TITLE_FILTERS));
    return;
  }
  const jobs = JSON.parse(process.argv[2]);
  const results = jobs.map((job) => passesFilter(job, DEFAULT_TITLE_FILTER));
  process.stdout.write(JSON.stringify(results));
}

main().catch((e) => {
  console.error('harness crashed:', (e && e.stack) || e);
  process.exitCode = 1;
});
