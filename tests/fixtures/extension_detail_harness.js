'use strict';

// Harness for the per-offer detail extractors (portal-detail-text D1):
//   node extension_detail_harness.js
// Runs each portal's `extractDetail` (called the way background.js injects it:
// with `portal.detailArgs`) over the synthetic pages in tests/fixtures/portals
// through the minimal DOM. Exit 0 on success.

const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
const { installDocument, uninstallDocument } = require('./minidom.js');

const failures = [];
function check(name, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) failures.push(`${name}: expected ${e}, got ${a}`);
}

const dir = path.join(__dirname, 'portals');
const load = (rel) => import(pathToFileURL(path.resolve(__dirname, '..', '..', 'extension', rel)).href);

function run(portal, file, title = '') {
  installDocument(fs.readFileSync(path.join(dir, file), 'utf8'));
  document.title = title;
  try {
    return portal.extractDetail(...(portal.detailArgs || []));
  } finally {
    uninstallDocument();
  }
}

async function main() {
  for (const id of ['indeed', 'tecnoempleo', 'jobfluent', 'manfred']) {
    const { default: portal } = await load(`portals/${id}.js`);
    check(`${id}: has a detail extractor`, typeof portal.extractDetail, 'function');
    check(`${id}: extractor is named (injected by function name)`, portal.extractDetail.name.length > 0, true);
    check(`${id}: detailArgs is an array`, Array.isArray(portal.detailArgs), true);
    const text = run(portal, `${id}_detail.html`);
    check(`${id}: returns the offer description`, typeof text === 'string' && text.includes('Requisitos: dos anos de experiencia'), true);
    check(`${id}: keeps the whole description`, typeof text === 'string' && text.includes('revision de codigo'), true);
    check(`${id}: leaves page chrome out`, typeof text === 'string' && !/Ofertas similares|Aviso legal|Otras ofertas|Ofertas relacionadas/.test(text), true);
    check(`${id}: under the bridge cap`, typeof text === 'string' && text.length < 20000, true);
    check(`${id}: a too-short page is null`, run(portal, 'detail_short.html'), null);
    check(`${id}: a challenge page is null`, run(portal, 'detail_challenge.html', 'Just a moment...'), null);
    const genuine = run(portal, 'detail_momento_captcha.html', 'Ingeniero de datos');
    check(`${id}: a short real offer mentioning "un momento" and "captcha" is extracted`, typeof genuine === 'string' && genuine.includes('captcha'), true);
    check(`${id}: a challenge title alone marks a short page as a challenge`, run(portal, 'detail_momento_captcha.html', 'Attention Required! | Cloudflare'), null);
    const bare = run(portal, 'detail_bare_body.html');
    check(`${id}: falls back to the body text`, typeof bare === 'string' && bare.includes('Responsabilidades'), true);
    const long = run(portal, 'detail_long.html');
    check(`${id}: a huge page is capped below 20000`, typeof long === 'string' && long.length <= 19000 && long.length > 10000, true);
  }

  // A portal panel that exists but is still empty (htmx not swapped yet) must
  // not fall back to the page chrome: null makes the job retry next round.
  const { default: foorilla } = await load('portals/foorilla.js');
  check('foorilla: an empty own panel is null, not the page chrome', run(foorilla, 'detail_unloaded_panel.html'), null);
  const { default: indeedLoose } = await load('portals/indeed.js');
  const loose = run(indeedLoose, 'detail_unloaded_panel.html');
  check('indeed (not strict): an empty own panel still degrades to the page text', typeof loose === 'string', true);

  const { default: indeed } = await load('portals/indeed.js');
  check('indeed: its own selector wins over the generic containers', indeed.detailArgs, ['#jobDescriptionText']);
  const { default: infojobs } = await load('portals/infojobs.js');
  check('infojobs keeps its dedicated extractor', infojobs.extractDetail.name, 'extractInfojobsDetail');

  if (failures.length) {
    console.error(`FAIL: ${failures.length} assertion(s) failed:`);
    for (const f of failures) console.error(' - ' + f);
    process.exitCode = 1;
  } else {
    process.stdout.write('OK: all detail extractor assertions passed\n');
  }
}

main().catch((e) => {
  console.error('harness crashed:', (e && e.stack) || e);
  process.exitCode = 1;
});
