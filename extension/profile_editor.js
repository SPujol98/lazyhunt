// Profile tab of the options page (onboarding-v2 T10): view and edit, after
// the bot /setup, the answers the bot keeps in the profile files.
//
// The bot owns the data and every rule: GET /profile answers the current
// values per section, the choices and the limits; POST /profile writes ONE
// section ({section, values}) through the same validated, all-or-nothing path
// as `/setup <section>`, and answers 400 {fields: {field: code}} when a value
// is refused. The page only shows what the bot sent and posts what the user
// typed; it never edits claims.json itself. Hand-written claims (ids without
// the `setup-` prefix) are listed read-only.
//
// The first half of this file is pure (tested in Node); mountProfileEditor
// builds the DOM with the page's own `el` and `tr` helpers.

export const PROFILE_SECTIONS = ['positions', 'level', 'background', 'location', 'salary', 'offer_langs'];
export const SECTION_FIELDS = {
  positions: ['families', 'kept_families', 'custom_titles'],
  level: ['level'],
  background: ['spoken_languages', 'skills', 'experience', 'education'],
  location: ['work_modes', 'work_city', 'country'],
  salary: ['min_salary', 'currency'],
  offer_langs: ['offer_langs'],
};
export const SETUP_PREFIX = 'setup-';

// Field error code (cva/onboarding/profile_edit.py ERROR_CODES) -> [locale key, English].
export const ERROR_TEXTS = {
  unknown_field: ['profileErrUnknown', 'The bot does not know this field.'],
  required: ['profileErrRequired', 'Required.'],
  type: ['profileErrType', 'Wrong value.'],
  choice: ['profileErrChoice', 'Pick one of the offered values.'],
  pick_one: ['profileErrPickOne', 'Pick at least one.'],
  text: ['profileErrText', 'From 1 to 100 characters, on one line.'],
  titles: ['profileErrTitles', 'Up to 10 titles of at most 100 characters, one per line.'],
  skills: ['profileErrSkills', 'From 1 to 20 skills of at most 50 characters, without commas.'],
  languages: ['profileErrLanguages', 'Up to 5 other languages of at most 40 characters.'],
  salary: ['profileErrSalary', 'A whole yearly amount, e.g. 30000 or 30k.'],
  currency: ['profileErrCurrency', 'A 3-letter currency code, e.g. EUR.'],
  company_without_role: ['profileErrCompany', 'A company needs a role.'],
};

const isObject = (v) => Boolean(v) && typeof v === 'object' && !Array.isArray(v);
const strings = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === 'string') : []);
const textOrNull = (v) => (typeof v === 'string' && v ? v : null);

// http(s)://host:port/profile next to the configured bridge URL, or null.
export function profileUrl(bridgeUrl) {
  try {
    const u = new URL(String(bridgeUrl || '').trim());
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
    return `${u.origin}/profile`;
  } catch (_) {
    return null;
  }
}

function claimList(value) {
  return (Array.isArray(value) ? value : [])
    .filter((c) => isObject(c) && typeof c.id === 'string')
    .map((c) => ({ id: c.id, category: textOrNull(c.category), text: typeof c.text === 'string' ? c.text : '' }));
}

// The bot's GET /profile body, shape-checked (missing parts get empty
// values); null when it is not a profile at all.
export function parseProfile(payload) {
  if (!isObject(payload) || !isObject(payload.sections)) return null;
  const s = payload.sections;
  const get = (name) => (isObject(s[name]) ? s[name] : {});
  const bg = get('background');
  const exp = isObject(bg.experience) ? bg.experience : null;
  const options = isObject(payload.options) ? payload.options : {};
  const claims = isObject(payload.claims) ? payload.claims : {};
  return {
    sections: {
      positions: {
        families: strings(get('positions').families),
        kept_families: strings(get('positions').kept_families),
        custom_titles: strings(get('positions').custom_titles),
      },
      level: { level: textOrNull(get('level').level) },
      background: {
        spoken_languages: (Array.isArray(bg.spoken_languages) ? bg.spoken_languages : [])
          .filter((l) => isObject(l) && typeof l.language === 'string' && typeof l.level === 'string')
          .map((l) => ({ language: l.language, level: l.level })),
        skills: strings(bg.skills),
        experience: exp && typeof exp.years === 'string'
          ? { years: exp.years, role: textOrNull(exp.role), company: textOrNull(exp.company) }
          : null,
        education: textOrNull(bg.education),
      },
      location: {
        work_modes: strings(get('location').work_modes),
        work_city: textOrNull(get('location').work_city),
        country: textOrNull(get('location').country),
      },
      salary: {
        min_salary: Number.isInteger(get('salary').min_salary) ? get('salary').min_salary : null,
        currency: textOrNull(get('salary').currency),
      },
      offer_langs: { offer_langs: strings(get('offer_langs').offer_langs) },
    },
    options: {
      families: (Array.isArray(options.families) ? options.families : [])
        .filter((f) => isObject(f) && typeof f.id === 'string')
        .map((f) => ({ id: f.id, label: typeof f.label === 'string' && f.label ? f.label : f.id, builtin: f.builtin === true })),
      levels: strings(options.levels),
      work_modes: strings(options.work_modes),
      offer_languages: strings(options.offer_languages),
      spoken_languages: strings(options.spoken_languages),
      language_levels: strings(options.language_levels),
      experience_years: strings(options.experience_years),
      currencies: strings(options.currencies),
    },
    claims: { setup: claimList(claims.setup), other: claimList(claims.other) },
  };
}

// The editable copy of the sections (text fields as typed).
export function draftFromProfile(profile) {
  const s = profile.sections;
  const exp = s.background.experience;
  return {
    positions: { ...s.positions, titlesText: s.positions.custom_titles.join('\n') },
    level: { ...s.level },
    background: {
      spoken_languages: s.background.spoken_languages.map((l) => ({ ...l })),
      skills: [...s.background.skills],
      years: exp ? exp.years : '',
      role: exp && exp.role ? exp.role : '',
      company: exp && exp.company ? exp.company : '',
      education: s.background.education || '',
    },
    location: { work_modes: [...s.location.work_modes], work_city: s.location.work_city || '', country: s.location.country || '' },
    salary: { amount: s.salary.min_salary === null ? '' : String(s.salary.min_salary), currency: s.salary.currency || '' },
    offer_langs: { offer_langs: [...s.offer_langs.offer_langs] },
  };
}

// A typed yearly amount like the /setup answer: 30000, 30.000, 30 000, 30k.
// '' is no minimum (null); anything else unreadable is sent as typed so the
// bot reports it next to the field.
export function parseSalary(text) {
  const raw = String(text ?? '').trim();
  if (!raw) return null;
  const value = raw.toLowerCase().replace(/[ .,]/g, '');
  const k = value.endsWith('k');
  const digits = k ? value.slice(0, -1) : value;
  if (!/^\d{1,9}$/.test(digits)) return raw;
  return Number(digits) * (k ? 1000 : 1);
}

const blankToNull = (text) => (String(text ?? '').trim() ? String(text).trim() : null);
const lines = (text) => String(text ?? '').split('\n').map((t) => t.trim()).filter(Boolean);

// The POST /profile body for one section of the draft.
export function sectionBody(section, draft) {
  const d = draft[section];
  let values;
  if (section === 'positions') {
    values = { families: [...d.families], kept_families: [...d.kept_families], custom_titles: lines(d.titlesText) };
  } else if (section === 'level') {
    values = { level: d.level };
  } else if (section === 'background') {
    values = {
      spoken_languages: d.spoken_languages
        .filter((l) => String(l.language).trim())
        .map((l) => ({ language: String(l.language).trim(), level: l.level })),
      skills: d.skills.map((x) => String(x).trim()).filter(Boolean),
      experience: { years: d.years || null, role: blankToNull(d.role), company: blankToNull(d.company) },
      education: blankToNull(d.education),
    };
  } else if (section === 'location') {
    values = { work_modes: [...d.work_modes], work_city: String(d.work_city).trim(), country: String(d.country).trim() };
  } else if (section === 'salary') {
    const amount = parseSalary(d.amount);
    values = { min_salary: amount, currency: amount === null ? null : blankToNull(d.currency) || 'EUR' };
  } else if (section === 'offer_langs') {
    values = { offer_langs: [...d.offer_langs] };
  } else {
    throw new Error(`unknown profile section: ${section}`);
  }
  return { section, values };
}

// {field: code} of a 400 answer; {} for anything else.
export function fieldErrors(payload) {
  if (!isObject(payload) || !isObject(payload.fields)) return {};
  return Object.fromEntries(Object.entries(payload.fields).filter(([, code]) => typeof code === 'string'));
}

// [locale key, English] of an error code (unknown codes get the generic text).
export function errorText(code) {
  return ERROR_TEXTS[code] || ERROR_TEXTS.type;
}

// Keeps `list` in the order of `order` with `value` toggled in or out.
export function toggled(list, value, order) {
  const set = new Set(list);
  if (set.has(value)) set.delete(value);
  else set.add(value);
  return order.filter((v) => set.has(v));
}

// --- DOM -------------------------------------------------------------------------

const LANGUAGE_NAMES = {
  es: ['profileLangEs', 'Spanish'], en: ['profileLangEn', 'English'], ca: ['profileLangCa', 'Catalan'],
  fr: ['profileLangFr', 'French'], de: ['profileLangDe', 'German'], it: ['profileLangIt', 'Italian'],
  pt: ['profileLangPt', 'Portuguese'],
};
const LEVEL_NAMES = {
  junior: ['profileLevelJunior', 'Junior'], mid: ['profileLevelMid', 'Mid'], senior: ['profileLevelSenior', 'Senior'],
};
const MODE_NAMES = {
  remote: ['profileModeRemote', 'Remote'], hybrid: ['profileModeHybrid', 'Hybrid'], onsite: ['profileModeOnsite', 'On-site'],
};
const YEARS_NAMES = {
  0: ['profileYears0', 'No experience yet'], lt1: ['profileYearsLt1', 'Less than 1 year'],
  '1-2': ['profileYears1to2', '1-2 years'], '3-5': ['profileYears3to5', '3-5 years'],
  '6-10': ['profileYears6to10', '6-10 years'], '10plus': ['profileYears10plus', 'More than 10 years'],
};
const CEFR_NAMES = { native: ['profileCefrNative', 'Native'] };
const OTHER_LANGUAGE = '__other__';

// Builds the Profile editor in `root` for `profile` (parseProfile output).
// `post(body)` sends one section and resolves to {status, payload} (status 0:
// unreachable); `onSaved(profile)` receives the bot's new profile.
export function mountProfileEditor(root, profile, { el, tr, post, onSaved, toast }) {
  const draft = draftFromProfile(profile);
  const opts = profile.options;
  const named = (map, code) => (map[code] ? tr(map[code][0], null, map[code][1]) : code);
  const errorNodes = {};

  const errorSlot = (section, field) => {
    const node = el('span', { class: 'hint hint-warn profile-error', role: 'alert', hidden: true });
    errorNodes[`${section}.${field}`] = node;
    return node;
  };
  const showErrors = (section, fields) => {
    for (const [key, node] of Object.entries(errorNodes)) {
      if (!key.startsWith(`${section}.`)) continue;
      const code = fields[key.slice(section.length + 1)];
      node.hidden = !code;
      node.textContent = code ? tr(errorText(code)[0], null, errorText(code)[1]) : '';
    }
  };
  const checks = (section, key, values, names) => el('div', { class: 'profile-checks' },
    ...values.map((value) => el('label', { class: 'check' },
      el('input', {
        type: 'checkbox', checked: draft[section][key].includes(value),
        onchange: () => { draft[section][key] = toggled(draft[section][key], value, values); },
      }),
      el('span', { text: typeof names === 'function' ? names(value) : named(names, value) }))));
  const textInput = (section, key, label, extra = {}) => el('label', { class: 'field' },
    el('span', { class: 'field-label', text: label }),
    el('input', {
      class: 'input', type: 'text', value: draft[section][key], maxlength: '100', ...extra,
      oninput: (e) => { draft[section][key] = e.currentTarget.value; },
    }),
    errorSlot(section, key));
  const select = (values, current, names, onchange, blank) => {
    const node = el('select', { class: 'select', onchange: (e) => onchange(e.currentTarget.value) },
      ...(blank ? [el('option', { value: '', text: blank })] : []),
      ...values.map((v) => el('option', { value: v, text: names(v) })));
    node.value = current || '';
    return node;
  };

  const block = (section, titleKey, title, ...body) => {
    const button = el('button', { type: 'button', class: 'btn btn-sm btn-primary', text: tr('profileSaveSection', null, 'Save') });
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const { status, payload } = await post(sectionBody(section, draft));
        if (status === 200 && isObject(payload) && parseProfile(payload.profile)) {
          showErrors(section, {});
          toast(payload.reloaded === false
            ? tr('profileSavedNotReloaded', null, 'Saved, but the bot could not reload it: check the bot chat.')
            : tr('profileSaved', null, 'Saved. The bot uses it from now on.'), payload.reloaded === false ? 'error' : 'ok');
          onSaved(parseProfile(payload.profile));
        } else if (status === 400 && Object.keys(fieldErrors(payload)).length) {
          showErrors(section, fieldErrors(payload));
          toast(tr('profileFixFields', null, 'Fix the marked fields.'), 'error');
        } else if (status === 0) {
          toast(tr('suggestUnreachable', null, 'The bot is not reachable. Is it running?'), 'error');
        } else {
          const detail = isObject(payload) && typeof payload.detail === 'string' ? payload.detail : `HTTP ${status}`;
          toast(isObject(payload) && payload.rolledBack === false
            ? tr('profileRollbackFailed', detail, 'Not saved ($1) and undoing it failed: check your profile folder.')
            : tr('profileSaveFailed', detail, 'Not saved ($1). Nothing was changed.'), 'error');
        }
      } finally {
        button.disabled = false;
      }
    });
    return el('fieldset', { class: 'profile-block', 'data-section': section },
      el('legend', { class: 'profile-legend', text: tr(titleKey, null, title) }),
      ...body,
      el('div', { class: 'profile-actions' }, button));
  };

  // Positions: the families the bot offers, plus own titles (a new family).
  const familyName = (id) => {
    const family = opts.families.find((f) => f.id === id);
    return family ? family.label : id;
  };
  const builtin = opts.families.filter((f) => f.builtin).map((f) => f.id);
  const declared = opts.families.filter((f) => !f.builtin).map((f) => f.id);
  const positions = block('positions', 'profilePositions', 'Target positions',
    checks('positions', 'families', builtin, familyName),
    declared.length ? checks('positions', 'kept_families', declared, familyName) : null,
    errorSlot('positions', 'families'),
    errorSlot('positions', 'kept_families'),
    el('label', { class: 'field' },
      el('span', { class: 'field-label', text: tr('profileOwnTitles', null, 'Other job titles (one per line): a new family') }),
      el('textarea', {
        class: 'textarea', rows: '2', spellcheck: 'false', value: draft.positions.titlesText,
        oninput: (e) => { draft.positions.titlesText = e.currentTarget.value; },
      }),
      errorSlot('positions', 'custom_titles')));

  const level = block('level', 'profileLevel', 'Level',
    el('label', { class: 'field' },
      select(opts.levels, draft.level.level, (v) => named(LEVEL_NAMES, v), (v) => { draft.level.level = v || null; },
        tr('profileNotStated', null, 'Not stated')),
      errorSlot('level', 'level')));

  const location = block('location', 'profileLocation', 'Work mode and location',
    checks('location', 'work_modes', opts.work_modes, MODE_NAMES),
    errorSlot('location', 'work_modes'),
    el('div', { class: 'grid-2' },
      textInput('location', 'work_city', tr('profileCity', null, 'City')),
      textInput('location', 'country', tr('profileCountry', null, 'Country'))));

  const salary = block('salary', 'profileSalary', 'Minimum salary',
    el('div', { class: 'grid-2' },
      el('label', { class: 'field' },
        el('span', { class: 'field-label', text: tr('profileSalaryAmount', null, 'Gross per year (empty: no minimum)') }),
        el('input', {
          class: 'input', type: 'text', inputmode: 'numeric', value: draft.salary.amount, placeholder: '30000',
          oninput: (e) => { draft.salary.amount = e.currentTarget.value; },
        }),
        errorSlot('salary', 'min_salary')),
      el('label', { class: 'field' },
        el('span', { class: 'field-label', text: tr('profileCurrency', null, 'Currency') }),
        el('input', {
          class: 'input', type: 'text', maxlength: '3', value: draft.salary.currency, placeholder: 'EUR', list: 'profileCurrencies',
          oninput: (e) => { draft.salary.currency = e.currentTarget.value; },
        }),
        el('datalist', { id: 'profileCurrencies' }, ...opts.currencies.map((c) => el('option', { value: c }))),
        errorSlot('salary', 'currency'))));

  const offerLangs = block('offer_langs', 'profileOfferLangs', 'Accepted offer languages',
    checks('offer_langs', 'offer_langs', opts.offer_languages, LANGUAGE_NAMES),
    errorSlot('offer_langs', 'offer_langs'));

  // Background: each row becomes one setup-* claim.
  const bg = draft.background;
  const langRows = el('div', { class: 'profile-list' });
  const drawLangs = () => {
    langRows.replaceChildren(...bg.spoken_languages.map((item, i) => {
      const common = opts.spoken_languages.includes(item.language);
      const typed = el('input', {
        class: 'input', type: 'text', maxlength: '40', value: common ? '' : item.language, hidden: common,
        placeholder: tr('profileLanguageTyped', null, 'Language'),
        oninput: (e) => { item.language = e.currentTarget.value; },
      });
      const choose = select([...opts.spoken_languages, OTHER_LANGUAGE], common ? item.language : OTHER_LANGUAGE,
        (v) => (v === OTHER_LANGUAGE ? tr('profileLanguageOther', null, 'Other…') : named(LANGUAGE_NAMES, v)),
        (v) => {
          item.language = v === OTHER_LANGUAGE ? typed.value : v;
          typed.hidden = v !== OTHER_LANGUAGE;
        });
      return el('div', { class: 'profile-item' }, choose, typed,
        select(opts.language_levels, item.level, (v) => named(CEFR_NAMES, v), (v) => { item.level = v; }),
        el('button', {
          type: 'button', class: 'btn btn-sm btn-ghost', text: tr('remove', null, 'Remove'),
          onclick: () => { bg.spoken_languages.splice(i, 1); drawLangs(); },
        }));
    }));
  };
  const skillRows = el('div', { class: 'profile-list' });
  const drawSkills = () => {
    skillRows.replaceChildren(...bg.skills.map((skill, i) => el('div', { class: 'profile-item' },
      el('input', {
        class: 'input', type: 'text', maxlength: '50', value: skill,
        oninput: (e) => { bg.skills[i] = e.currentTarget.value; },
      }),
      el('button', {
        type: 'button', class: 'btn btn-sm btn-ghost', text: tr('remove', null, 'Remove'),
        onclick: () => { bg.skills.splice(i, 1); drawSkills(); },
      }))));
  };
  drawLangs();
  drawSkills();
  const background = block('background', 'profileBackground', 'Languages, skills and experience',
    el('h4', { class: 'profile-sub', text: tr('profileSpokenLanguages', null, 'Languages you speak') }),
    langRows,
    el('button', {
      type: 'button', class: 'btn btn-sm', text: tr('profileAddLanguage', null, 'Add language'),
      onclick: () => { bg.spoken_languages.push({ language: opts.spoken_languages[0] || '', level: opts.language_levels[0] || '' }); drawLangs(); },
    }),
    errorSlot('background', 'spoken_languages'),
    el('h4', { class: 'profile-sub', text: tr('profileSkills', null, 'Main skills') }),
    skillRows,
    el('button', {
      type: 'button', class: 'btn btn-sm', text: tr('profileAddSkill', null, 'Add skill'),
      onclick: () => { bg.skills.push(''); drawSkills(); },
    }),
    errorSlot('background', 'skills'),
    el('h4', { class: 'profile-sub', text: tr('profileExperience', null, 'Experience') }),
    el('div', { class: 'grid-3' },
      el('label', { class: 'field' },
        el('span', { class: 'field-label', text: tr('profileYears', null, 'Years') }),
        select(opts.experience_years, bg.years, (v) => named(YEARS_NAMES, v), (v) => { bg.years = v; }, '—')),
      textInput('background', 'role', tr('profileRole', null, 'Last role (optional)')),
      textInput('background', 'company', tr('profileCompany', null, 'Company (optional)'))),
    errorSlot('background', 'experience'),
    textInput('background', 'education', tr('profileEducation', null, 'Highest education (optional)')));

  // Claims: what the bot generated from the answers above, then the rest.
  const claimItem = (c) => el('li', { class: 'profile-claim' },
    el('code', { text: c.id }), el('span', { text: c.text }));
  const claims = el('div', { class: 'profile-block' },
    el('h3', { class: 'profile-legend', text: tr('profileClaimsHeading', null, 'Claims from your answers') }),
    el('p', { class: 'hint', text: tr('profileClaimsIntro', null, 'Generated from the section above (ids starting with setup-): edit them there.') }),
    profile.claims.setup.length
      ? el('ul', { class: 'profile-claims' }, ...profile.claims.setup.map(claimItem))
      : el('p', { class: 'empty-row muted', text: tr('profileClaimsNone', null, 'None yet.') }),
    el('details', { class: 'preview' },
      el('summary', {},
        el('span', { text: tr('profileOtherClaims', null, 'Hand-written claims (read-only)') }), ' ',
        el('span', { class: 'chip chip-plain', text: String(profile.claims.other.length) })),
      el('p', { class: 'hint', text: tr('profileOtherClaimsHint', null, 'The extension never changes these: edit claims.json in your profile folder by hand.') }),
      el('ul', { class: 'profile-claims' }, ...profile.claims.other.map(claimItem))));

  root.replaceChildren(positions, level, background, location, salary, offerLangs, claims);
}
