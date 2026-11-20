'use strict';

// Node harness for extension/profile_editor.js (onboarding-v2 T10).
// Invoked by tests/test_extension_profile.py.
//
//   node extension_profile_harness.js helpers    pure helpers, exit 1 on failure
//   node extension_profile_harness.js contract   prints {sections, errors} as JSON
//   node extension_profile_harness.js roundtrip  reads a GET /profile body on
//       stdin, prints the POST bodies the page would send for every section
//       without edits

const path = require('path');
const { pathToFileURL } = require('url');

const failures = [];
function check(name, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) failures.push(`${name}: expected ${e}, got ${a}`);
}
const load = (rel) => import(pathToFileURL(path.resolve(__dirname, '..', '..', 'extension', rel)).href);

function readStdin() {
  return new Promise((resolve) => {
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => { data += chunk; });
    process.stdin.on('end', () => resolve(data));
  });
}

const PROFILE = {
  sections: {
    positions: { families: ['ai'], kept_families: ['data_analyst'], custom_titles: [] },
    level: { level: 'mid' },
    background: {
      spoken_languages: [{ language: 'es', level: 'native' }, { language: 'Basque', level: 'B1' }],
      skills: ['Python', 'SQL'],
      experience: { years: '3-5', role: 'Data Engineer', company: null },
      education: null,
    },
    location: { work_modes: ['remote'], work_city: 'Valencia', country: 'Spain' },
    salary: { min_salary: 40000, currency: 'EUR' },
    offer_langs: { offer_langs: ['es', 'en'] },
  },
  options: {
    families: [{ id: 'ai', label: 'AI', builtin: true }, { id: 'data_analyst', label: 'Data Analyst', builtin: false }],
    levels: ['junior', 'mid', 'senior'],
    work_modes: ['remote', 'hybrid', 'onsite'],
    offer_languages: ['es', 'en'],
    spoken_languages: ['es', 'en'],
    language_levels: ['native', 'C2'],
    experience_years: ['0', '3-5'],
    currencies: ['EUR', 'USD'],
  },
  limits: { text: 100 },
  claims: {
    setup: [{ id: 'setup-skill-python', category: 'skill', text: 'Python' }],
    other: [{ id: 'identity-contact', category: 'identity', text: 'Alex' }, { id: 7 }],
  },
};

async function helpers() {
  const m = await load('profile_editor.js');

  // --- rule: the endpoint lives next to the configured bridge URL -------------------
  check('url from bridge', m.profileUrl('http://127.0.0.1:8765/bridge'), 'http://127.0.0.1:8765/profile');
  check('url keeps port', m.profileUrl(' http://localhost:9000/x?y=1 '), 'http://localhost:9000/profile');
  check('url refuses other schemes', [m.profileUrl('file:///etc/passwd'), m.profileUrl('nope'), m.profileUrl('')], [null, null, null]);

  // --- rule: the bot's answer is shape-checked -------------------------------------
  check('junk is not a profile', [m.parseProfile(null), m.parseProfile([]), m.parseProfile({ sections: 1 })], [null, null, null]);
  const parsed = m.parseProfile(PROFILE);
  check('parsed sections', parsed.sections, PROFILE.sections);
  check('claims without a string id are dropped', parsed.claims.other.map((c) => c.id), ['identity-contact']);
  const sparse = m.parseProfile({ sections: { level: { level: 3 }, salary: { min_salary: 1.5 } } });
  check('wrong types become empty values', [sparse.sections.level.level, sparse.sections.salary.min_salary,
    sparse.sections.background.experience, sparse.options.levels], [null, null, null, []]);

  // --- rule: an unedited draft posts back the same values ---------------------------
  const draft = m.draftFromProfile(parsed);
  check('positions body', m.sectionBody('positions', draft),
    { section: 'positions', values: { families: ['ai'], kept_families: ['data_analyst'], custom_titles: [] } });
  check('level body', m.sectionBody('level', draft), { section: 'level', values: { level: 'mid' } });
  check('background body', m.sectionBody('background', draft), {
    section: 'background',
    values: {
      spoken_languages: PROFILE.sections.background.spoken_languages,
      skills: ['Python', 'SQL'],
      experience: { years: '3-5', role: 'Data Engineer', company: null },
      education: null,
    },
  });
  check('location body', m.sectionBody('location', draft).values, PROFILE.sections.location);
  check('salary body', m.sectionBody('salary', draft).values, PROFILE.sections.salary);
  check('offer languages body', m.sectionBody('offer_langs', draft).values, PROFILE.sections.offer_langs);
  for (const section of m.PROFILE_SECTIONS) {
    check(`${section} sends exactly its fields`, Object.keys(m.sectionBody(section, draft).values), m.SECTION_FIELDS[section]);
  }

  // --- rule: edits become the values the bot validates ------------------------------
  draft.positions.titlesText = ' Data Analyst \n\n BI Developer ';
  check('own titles: one per line, trimmed', m.sectionBody('positions', draft).values.custom_titles, ['Data Analyst', 'BI Developer']);
  draft.background.skills = ['Go', '  ', ' Rust '];
  draft.background.years = '';
  draft.background.role = '  ';
  draft.background.spoken_languages.push({ language: '  ', level: 'B1' });
  const bg = m.sectionBody('background', draft).values;
  check('blank skills dropped', bg.skills, ['Go', 'Rust']);
  check('no years is null (the bot asks for it)', bg.experience, { years: null, role: null, company: null });
  check('blank language rows dropped', bg.spoken_languages.length, 2);
  draft.salary.amount = '';
  check('empty salary is no minimum', m.sectionBody('salary', draft).values, { min_salary: null, currency: null });
  draft.salary.amount = '35k';
  draft.salary.currency = '';
  check('salary defaults to EUR', m.sectionBody('salary', draft).values, { min_salary: 35000, currency: 'EUR' });
  check('parseSalary forms', ['30000', '30.000', '30 000', '30k', '', 'lots', '1234567890'].map(m.parseSalary),
    [30000, 30000, 30000, 30000, null, 'lots', '1234567890']);
  let threw = false;
  try { m.sectionBody('contact', draft); } catch (_) { threw = true; }
  check('unknown section throws', threw, true);

  // --- rule: field errors are shown next to their field ----------------------------
  check('field errors', m.fieldErrors({ error: 'invalid fields', fields: { skills: 'skills', x: 3 } }), { skills: 'skills' });
  check('no field errors', [m.fieldErrors(null), m.fieldErrors({ error: 'x' })], [{}, {}]);
  check('error text', m.errorText('salary')[0], 'profileErrSalary');
  check('unknown code gets the generic text', m.errorText('zzz')[0], 'profileErrType');
  check('toggle keeps the offered order', [m.toggled(['b'], 'a', ['a', 'b', 'c']), m.toggled(['a', 'b'], 'a', ['a', 'b'])],
    [['a', 'b'], ['b']]);
}

// A tiny fake DOM for mountProfileEditor: el() as the options page calls it.
function fakeEl(tag, props = {}, ...children) {
  const node = {
    tag, attrs: {}, listeners: {}, children: [], hidden: false, disabled: false, value: '', checked: false, textContent: '',
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    async fire(type, extra = {}) {
      for (const fn of this.listeners[type] || []) await fn({ currentTarget: this, target: this, ...extra });
    },
    replaceChildren(...nodes) { this.children = nodes.filter((n) => n !== null && n !== undefined); },
    append(...nodes) { this.children.push(...nodes.filter((n) => n !== null && n !== undefined)); },
  };
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null) continue;
    if (key === 'text') node.textContent = value;
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (['value', 'checked', 'type', 'disabled', 'hidden', 'title', 'placeholder'].includes(key)) node[key] = value;
    else node.attrs[key] = String(value);
  }
  node.append(...children);
  return node;
}
const walk = (node, out = []) => {
  if (node && typeof node === 'object') {
    out.push(node);
    for (const child of node.children || []) walk(child, out);
  }
  return out;
};

async function mount() {
  const m = await load('profile_editor.js');
  const root = fakeEl('div');
  const posted = [];
  const toasts = [];
  let saved = null;
  let answer = { status: 400, payload: { error: 'invalid fields', fields: { level: 'choice' } } };
  m.mountProfileEditor(root, m.parseProfile(PROFILE), {
    el: fakeEl,
    tr: (key, subs, english) => english,
    toast: (text, kind) => toasts.push([text, kind]),
    post: async (body) => { posted.push(body); return answer; },
    onSaved: (profile) => { saved = profile; },
  });
  const blocks = root.children.filter((n) => n.attrs['data-section']);
  check('one block per section', blocks.map((b) => b.attrs['data-section']), ['positions', 'level', 'background', 'location', 'salary', 'offer_langs']);
  const levelBlock = blocks[1];
  const select = walk(levelBlock).find((n) => n.tag === 'select');
  check('level select shows the current value', select.value, 'mid');
  await select.fire('change', { currentTarget: { value: 'senior' } });
  const save = walk(levelBlock).find((n) => n.tag === 'button');
  await save.fire('click');
  check('save posts that section only', posted, [{ section: 'level', values: { level: 'senior' } }]);
  const error = walk(levelBlock).find((n) => n.attrs.role === 'alert');
  check('a field error is shown next to its field', [error.hidden, error.textContent], [false, 'Pick one of the offered values.']);
  check('and a toast', toasts.at(-1), ['Fix the marked fields.', 'error']);
  answer = { status: 200, payload: { saved: true, reloaded: true, profile: PROFILE } };
  await save.fire('click');
  check('a save hides the errors', error.hidden, true);
  check('and hands the new profile over', saved && saved.sections.level.level, 'mid');
  answer = { status: 500, payload: { error: 'write failed', detail: 'OSError: disk full', rolledBack: true } };
  await save.fire('click');
  check('a failed write says nothing changed', toasts.at(-1), ['Not saved ($1). Nothing was changed.', 'error']);
  // Background: removing a skill row removes it from the next POST.
  const bgBlock = blocks[2];
  const removeSkill = walk(bgBlock).filter((n) => n.tag === 'button' && n.textContent === 'Remove').at(-1);
  await removeSkill.fire('click');
  const bgSave = walk(bgBlock).filter((n) => n.tag === 'button').at(-1);
  await bgSave.fire('click');
  check('removed skill is not posted', posted.at(-1).values.skills, ['Python']);
  check('hand-written claims listed read-only', walk(root).filter((n) => n.tag === 'code').map((n) => n.textContent),
    ['setup-skill-python', 'identity-contact']);
}

async function main() {
  const mode = process.argv[2] || 'helpers';
  if (mode === 'contract') {
    const m = await load('profile_editor.js');
    process.stdout.write(JSON.stringify({ sections: m.SECTION_FIELDS, errors: Object.keys(m.ERROR_TEXTS) }));
    return;
  }
  if (mode === 'roundtrip') {
    const m = await load('profile_editor.js');
    const profile = m.parseProfile(JSON.parse(await readStdin()));
    if (!profile) throw new Error('not a profile');
    const draft = m.draftFromProfile(profile);
    process.stdout.write(JSON.stringify(m.PROFILE_SECTIONS.map((s) => m.sectionBody(s, draft))));
    return;
  }
  await helpers();
  await mount();
  if (failures.length) {
    console.error(failures.join('\n'));
    process.exit(1);
  }
  console.log('profile editor harness: ok');
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
