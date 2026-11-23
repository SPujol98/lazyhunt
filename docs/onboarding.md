# Guided setup in Telegram (`/setup`, `/configurar`)

The guided setup asks for your profile in Telegram, in about 10 minutes, so
you never have to write the profile files by hand. It runs on the first start
(setup mode, after `uv run cli.py init` with an empty profile), and you can
run it again later, whole or one section at a time, to change your answers.
Where it fits in the whole first run: [getting-started.md](getting-started.md).

## How it works

- You get one question per message. Use the buttons when the answer is a
  choice, and type the answer when it is text. Each question starts with
  `Setup n/20` (`n/total` of a section with `/setup <section>`): the total
  never changes, a follow-up (each language's level, the company, the
  currency) keeps the number of the question it follows, and when no base
  CV is asked the summary comes right after the portals.
- Every step has **Cancel**. Every step after the first also has **Back**.
- Nothing is saved until you press **Confirm** on the summary. **Edit** on
  the summary takes you to a single answer and then back to the summary.
- Confirm is all or nothing: if any write fails, every file is put back as
  it was and the reply says so (see [After Confirm](#after-confirm)).
- A setup left idle for 30 minutes expires (the STAR flow works the same way).
  Send `/setup` again to start over.
- When a step expects text, your message goes to the setup first, ahead of a
  pending pasted offer, outcome note or STAR answer. Starting `/setup` drops
  any of those that are still pending.

## Re-running it: `/setup` and `/setup <section>`

Every re-run starts **prefilled** with what your profile already says. Text
questions show a **Keep …** button with the current value, and choices show
the current one ticked, so you only change what you want.

`/setup <section>` (`/configurar <sección>`) asks only one part of the
questionnaire, then shows a short summary of that part and **Confirm**. It
needs a profile that is already set up; before that, run `/setup` alone.

| Section (EN) | Sección (ES) | Questions | What Confirm writes |
|---|---|---|---|
| `contact` | `contacto` | name, email, phone, city, LinkedIn | `profile.toml` name; contact in `onboarding_answers.json` |
| `positions` | `puestos` | target positions | `preferences.toml [families]` (the roles of the [suggested searches](#suggested-searches)) |
| `level` | `nivel` | level | `[search] level` |
| `background` | `trayectoria` | spoken languages and levels, skills, experience, education | the `setup-*` claims |
| `location` | `ubicacion` | work modes, work city, country | `[search] work_modes`, `[location] city`, `country` (the places of the [suggested searches](#suggested-searches)) |
| `salary` | `salario` | minimum salary, currency | `[search] min_salary`, `currency` |
| `offer-languages` | `idiomas-ofertas` | accepted offer languages | `[gates] allowed_languages` |
| `portals` | `portales` | portals | `onboarding_answers.json` and `config.local.toml` `enabled_portals` |
| `cvs` | `cvs` | base CV PDFs | `cv_base_pdfs/` and its manifest |

- Names are not case- or accent-sensitive, and a few aliases work too
  (`sueldo`, `ubicación`, `skills`, `cv`, `offer languages`...). An unknown
  name gets the list of sections.
- Everything outside the section stays as it was: a file the section does
  not change keeps its exact bytes, and other keys keep their values. The
  `setup-*` claims are replaced only by `/setup` and `/setup background`, so
  claims you edited by hand survive every other section.
- Confirm is the same as for the whole questionnaire: validated, and
  all-or-nothing (a failure puts every file back).
- `contact` (and the full `/setup`) also updates the identity claim and
  the phone claim of an existing `claims.json`, in the same all-or-nothing
  Confirm, when `/setup` generated them. They count as generated only while
  they are exactly what the last Confirm's contact answers
  (`onboarding_answers.json`) produce. If you edited them by hand, or the
  profile has no `onboarding_answers.json` (so `/setup` did not create
  them), they are kept byte for byte and the reply tells you which of the
  two it was; change your contact details in that claim's text in
  `claims.json` yourself.

Where the current values come from, the later source winning:
`onboarding_answers.json` (the last Confirm), `claims.json` (name, email and
LinkedIn from the identity claim, and the background from the `setup-*`
claims when the record lacks them), `profile.toml` (`[identity] name`) and
`preferences.toml` (level, work modes, salary, location, offer languages, and
the declared families, offered preselected). Own titles typed in an earlier
run are not prefilled: their family is one of the declared ones.

## Change it later

Two ways to change your answers after the first setup; both write through
the same validated, all-or-nothing path and the bot uses the result at once:

- **In the bot:** `/setup <section>` (above) re-asks one part of the
  questionnaire, prefilled with the current values.
- **In the extension:** **Options › Profile** › **Load from the bot** shows
  the current values and lets you edit them section by section, each with
  its own **Save**: target positions (built-in and declared families, plus
  own titles), level, spoken languages and their levels, skills,
  experience, education, work modes, city and country, minimum salary and
  currency, and accepted offer languages. Each spoken language, skill,
  experience and education line is one `setup-*` claim: adding, editing or
  removing a row adds, edits or removes that claim. A value the bot refuses
  is marked next to its field and nothing is written. Claims you wrote by
  hand (any id without the `setup-` prefix) are listed read-only: edit
  `claims.json` by hand, the extension never changes them.

The Profile tab needs the bridge (URL and secret in **Options › Schedule and
bot**) and a profile that is already set up; contact data, portals and base
CVs stay in `/setup <section>`.

## Steps

1. Bot language (English / Español)
2. Contact details: name, email, phone (optional), city and LinkedIn URL
3. Target positions: the built-in families (AI, Backend) and/or your own
   titles ("Other titles")
4. Level: junior, mid or senior
5. Spoken languages: tick the common ones (Spanish, English, Catalan, French,
   German, Italian, Portuguese) or type others ("Other languages"), then pick
   a level for each one: Native, C2, C1, B2, B1 or A2
6. Main skills or stack, separated by commas (1-30 items, at most 50
   characters each; spaces are normalized and repeats dropped). Tip: the
   10-15 strongest work best.
7. Experience: years (0, <1, 1-2, 3-5, 6-10, 10+), then your last role and
   its company (both optional: Skip; the company is asked only with a role)
8. Education, up to 5 entries (optional: Skip): one per line or separated
   by `;`; without either, separated by commas. Use one line per entry when
   an entry has a comma (`BSc Computer Science, UPV`). Each entry, at most
   100 characters, becomes its own claim.
9. Work modes (remote, hybrid, on-site), then the city you want to work in
   (your city from step 2 is offered) and the country: when the city is a
   known place (every Spanish city and region, and a few big foreign cities)
   its country is one tap ("Spain (from Barcelona)"), with "Other…" to type
   another; an unknown city proposes nothing, and a re-run keeps the saved
   country
10. Minimum gross annual salary (or Skip). When the profile's
    `salary_reference.toml` has a range for one of your positions, your
    level (junior or mid) and your work city (Barcelona, Madrid, or another
    Spanish city with remote work accepted, as `spain_remote`), the step
    shows "Typical for <role> <level> in <city>: <low>–<high> EUR
    gross/year (source: …). Use <low>?" with a button that takes the low
    end. Without such a row (no table, senior level, a country other than
    Spain, a city that does not map) it says there is no reference: no
    number is ever invented. Then the currency: buttons EUR, USD, GBP, CHF
    and Other (type a 3-letter ISO code). The one ticked by default comes
    from the country: the UK is GBP, the US USD, Switzerland CHF, anything
    else (Spain and the euro area included) EUR. One currency on purpose:
    the one your minimum salary is in.
11. Languages an offer may be written in
12. Portals to enable. Before a portal is enabled, you see the terms warning
    the browser extension shows for it.
13. Base CVs: one per position type × language. For each one, upload
    your PDF, create one with lazyhunt from your answers, or leave it for
    later; optionally a base cover letter (see below)
14. Summary, then Confirm or Edit

## After Confirm

A Confirm of the whole questionnaire, in one all-or-nothing step:

1. writes `preferences.toml` and `profile.toml` ([Where the answers go](#where-the-answers-go));
2. stores your base CV PDFs and their manifest ([Base CV PDFs](#base-cv-pdfs-step-13));
3. creates the files of the [minimal profile](#the-minimal-profile) that
   are still missing (`claims.json`, `synonyms.json`, the judge files);
4. sets `enabled_portals` in `config.local.toml` to the portals you chose.

If any of these fails, every file is put back as it was, your uploads stay,
and the reply says "Nothing was changed": fix the cause it names and press
**Confirm** again, with no need to answer or upload anything twice. If
undoing fails too (rare: a full disk or a permission change mid-write), the
reply says which file to check; each rewritten TOML file keeps its previous
version as `<name>.bak`.

When it succeeds, the bot loads the new profile at once: in setup mode it
switches to normal mode right after Confirm, with no restart and without
waiting for another message (the bridge then serves your portals and
suggested searches). The reply lists the
active portals (grant each one's browser permission in the extension's
**Options › Portals**; a portal is scanned only when it is on in the bot and
in the extension) and ends with the next step: **Options › Searches ›
Suggest from my profile** in the extension. A `/setup <section>` reply has
no next step.

## Suggested searches

**Suggest from my profile** (extension **Options › Searches**) asks the bot
for ready-to-edit searches (`GET /searches/suggested`). They are built from
your answers, up to 8 of them:

- **Keywords:** the roles of each position you chose: the built-in AI and
  Backend roles, and your own titles (`search_roles`).
- **Places:** your work city when you accept on-site or hybrid work (the
  country when no city is set), plus one remote search when you accept
  remote work. The remote search is written `remoto` when you accept offers
  in Spanish or Catalan, and `remote` otherwise; the portal adapters treat
  both as their remote filter.
- **Portals:** the portals enabled on the bot.

Nothing is written for this: the places are derived each time the bot reads
`preferences.toml`, so `/setup location`, `/setup positions` and the Profile
tab change the suggestions at once. A `[search_targets] locations` list you
write by hand always wins over the answers, and a profile that sets neither
`[search] work_modes` nor `[location] city`/`country` keeps the default
places (`Barcelona`, `remoto`).

## Where the answers go

| Answer | File and key |
| --- | --- |
| Positions (built-in) | `preferences.toml` `[families.ai]` / `[families.backend]`; `languages` = the accepted offer languages the family can write |
| Positions (already declared) | A re-run offers every `[families.<id>]` already in `preferences.toml`, preselected and labelled by its `label`. A family you leave selected keeps all its keys as written; one you deselect is removed |
| Positions (none declared) | A `preferences.toml` without `[families]` uses the built-in AI and Backend families, so a re-run and the Profile tab show both selected. Keeping both (and no own titles) leaves the file without `[families]`, so nothing changes; deselecting one, or adding own titles, declares the families you keep |
| Own titles | `[families.<slug of the first title>]`: `label`, `title_terms`, `search_roles`, `languages` |
| Level, work modes | `[search] level`, `work_modes` |
| Salary, currency | `[search] min_salary`, `currency` (both removed on Skip) |
| Work city, country | `[location] city`, `country` |
| Offer languages | `[gates] allowed_languages` |
| Name | `profile.toml` `[identity] name` and `file_slug` (derived from the name) |
| Email, phone, city, LinkedIn | `onboarding_answers.json` (`contact`), for the step that generates your claims, and the identity and phone claims when `/setup` generated them (see Re-running it, above); never written to `preferences.toml` |
| Spoken languages, skills, experience, education | `onboarding_answers.json` (`background`) and the `setup-*` claims of `claims.json` (see [The minimal profile](#the-minimal-profile)) |
| Portals | `onboarding_answers.json` and `config.local.toml` `enabled_portals` (only that line changes; see below) |
| Base CV PDFs | `cv_base_pdfs/<name>.pdf` and `cv_base_pdfs/manifest.json` (see [Base CV PDFs](#base-cv-pdfs-step-13)) |
| Bot language | `config.local.toml` `ui_language` (saved only when it changes, with the same writer `cli.py init` uses) |

Both TOML files are written by `cva/profile_writer.py`. The writer validates
the result with the strict loaders, keeps the previous file as `<name>.bak`
and replaces the file atomically. Keys the setup does not ask about are kept.
Free-text answers with control characters are refused.
Comments in these files are not kept. `onboarding_answers.json` is created
with mode `0600` because it holds your contact data.

`onboarding_answers.json` (schema 2) holds:

```json
{
  "schema": 2,
  "ui_language": "en",
  "contact": {"name": "…", "email": "…", "phone": null, "city": "…", "linkedin": "…"},
  "background": {
    "spoken_languages": [{"language": "es", "level": "native"}, {"language": "Basque", "level": "B1"}],
    "skills": ["Python", "SQL"],
    "experience": {"years": "3-5", "role": "Backend Developer", "company": "Acme"},
    "education": ["BSc Computer Science"]
  },
  "portals": ["linkedin"],
  "answers": {"…": "every answer as given"}
}
```

`language` is a code (`es`, `en`, `ca`, `fr`, `de`, `it`, `pt`) for a common
language and the name as typed for any other; a typed name that matches a
common one (`english`, `Inglés`) counts as that one. `level` is one of
`native`, `C2`, `C1`, `B2`, `B1`, `A2`. `years` is one of `0`, `lt1`, `1-2`,
`3-5`, `6-10`, `10plus`. `experience` is `null` without a years answer;
`role` and `company` are `null` when skipped; `education` is a list of
entries (empty when skipped). An older file whose `education` is one line
still loads: that line is one entry, never split again.

In the full bot, the new preferences and portals take effect immediately
(`reload_preferences`; the bridge reads `enabled_portals` on every request).
Some service texts change only after a restart.

## Base CV PDFs (step 13)

The step starts by saying what base CVs are for: the bot sends this CV,
adapted, for each offer; one per position type × language. For the slot
marked 👈 it offers three buttons:

- **📎 Upload my PDF**: send your own PDF as a file (the rules below).
- **✨ Create one with lazyhunt**: pick one of three templates (Classic:
  centred header and ruled sections; Modern: the name in blue, coloured
  headings; Compact: smaller type, the most content on one page). The PDF is
  built only from your answers (name, contact, target position, a profile
  line from your level and years, last role and company, education entries,
  skills, spoken languages with levels): empty sections are left out,
  nothing is invented, and it is one column with selectable text, standard
  headings, no photo and no tables (ATS-friendly). It is a starting point:
  add projects and achievements later. It is stored exactly like an upload
  (same pending area, same file name and manifest `sha256`), so delivery
  works unchanged; on Confirm it is drawn again from your final answers.
- **Not now**: leave that slot without a CV for now.

**Skip all** first warns "Without a CV the bot cannot send any — create one
now?" (listing the slots still without one), with **Create one now** and
**Skip anyway**. **➕ Create another version** adds a base CV for another
position type or language; it also adds that position to your searches or
that language to the accepted offer languages when they were not there (full
`/setup` only). When the profile has no `cover_letter_base.toml`, **✨ Create
a base cover letter** writes one on Confirm from your answers (Spanish,
English and Catalan; short and plain: who you are, your last role, your main
skills); an existing file is never offered or overwritten, and it is part of
the all-or-nothing Confirm.

The rest of this section is about uploads. The bot asks for one PDF
for each position you chose and each CV language that position ends up with
(the accepted offer languages it can write: Spanish, English, Catalan). These
are the `<ROLE>_<LANG>` keys of `cv_base_pdfs/manifest.json`, for example
`AI_ES` or `DATA_ANALYST_EN`.

- Send each PDF as a file (📎 → File), not as a photo. The list shows each
  position and language with ⏳ (waiting), ✅ (received) or ⏭ (skipped), and
  👈 marks the one the next PDF is for. Tap a line to choose another one, or
  to replace a PDF you already sent.
- When you run `/setup` again, a position and language the profile already
  has a base CV for (listed in `cv_base_pdfs/manifest.json`, with a PDF that
  still matches its `sha256`) shows as ✅ (already saved) and needs no upload;
  tap its line to replace it. Only the ones with no PDF at all are reported as
  without a CV.
- **Skip this one** and **Skip all** leave a position and language without a
  CV. **Done** treats the ones you did not send the same way. The summary and
  the reply after Confirm list them: the bot cannot deliver a CV there until
  you add one (run `/setup` again, or edit the manifest by hand).
- A PDF is refused when its name or type is not PDF, when it is larger than
  10 MB (checked before the download), when it does not start with `%PDF-`, or
  when pypdf cannot read it or it has more than 10 pages.
- Until you confirm, uploads wait in `.onboarding_uploads/` inside the profile
  directory (git ignores that folder everywhere, demo profile included).
  Cancel, a setup that expires, and a new `/setup` delete them.
- Confirm is all or nothing. If anything fails (writing the answers, storing
  the PDFs, the manifest, or creating the minimal profile), every file it
  wrote is put back as it was, the uploads stay, and the reply says nothing
  was changed: fix the cause and press Confirm again, without sending the
  PDFs again. The uploads are deleted only after a Confirm that succeeds.
- On Confirm, each PDF is copied to `cv_base_pdfs/` as
  `CV_<position label>_<file_slug>[_<LANG>].pdf`, with no suffix for Spanish,
  like the demo profile (`CV_AI_Jose_Perez.pdf`, `CV_AI_Jose_Perez_EN.pdf`).
  The name never comes from the file you sent. A name that already belongs to
  another variant becomes `CV_<KEY>_<file_slug>.pdf`, so no other PDF is
  overwritten.
- The manifest is merged and written atomically: each uploaded variant gets
  `file` and the `sha256` of the stored file. Variants you did not replace,
  extra keys such as `delivery_name`, and top-level keys such as
  `environment` are kept. If the manifest cannot be read, nothing is
  overwritten and Confirm reports the error. A new manifest holds only
  `variants`; `environment` is optional and only `scripts/build_demo_pdfs.py`
  reads it, for the demo PDFs.
- Replacing a variant does not delete its previous PDF if it had another
  name.
- Reading the PDFs to propose claims is a later phase (P2).

## Notes and limits

- The setup needs a profile directory (`profile_dir` in `config.local.toml`).
  If you use the legacy layout, which has no `profile_dir`, `/setup` tells
  you to run `uv run cli.py init` first, and `/help` and the command menu
  do not offer `/setup` there.
- Confirm (the whole questionnaire and `/setup portals`) activates the
  portals you chose: it sets `enabled_portals` in `config.local.toml` to
  exactly those portal ids. Only portals the bot knows can be written. The
  wizard's writer changes that one line (a multi-line array included,
  keeping a comment at its end) and keeps every other line and comment
  byte for byte; a symlinked `config.local.toml` (dotfiles) is written
  through its link, which stays a link; the result, over
  `config.toml`, must load with the bot's settings validators before the
  file is replaced, and the file is part of the all-or-nothing Confirm. The
  running bot serves the new list at once (the bridge's `/config`, which the
  extension syncs every round; no restart), and setup mode hands it to the
  full bot. The reply lists the active portals: grant the browser
  permission for each one in the extension (**Options › Portals**). The
  step is prefilled from `enabled_portals` when the file sets it. A
  `CVA_ENABLED_PORTALS` environment variable still overrides the file at
  the next start.
- In setup mode, the bot needs `claims.json`, `synonyms.json` and the judge
  files before it can run normally. Confirm creates them (the
  [minimal profile](#the-minimal-profile)); if one is still missing, the
  reply lists it.
- `/setup` does not read your CV PDFs yet, and it asks for no projects: add
  project claims to `claims.json` by hand for now. Reading a CV to propose
  claims is a later phase.
- Location outside Catalonia is matched by city or country name, with no
  distances: an on-site offer in another city is flagged on its card, never
  discarded (see the [profiles README](../profiles/README.md#what-you-accept-search-and-location)).
- Work mode and minimum salary never hide an offer: a mismatch adds a ⚠️ line
  to its card.

## The minimal profile

After Confirm, the setup creates the files the bot needs that are still
missing. It builds them only from your answers, and it never changes a file
that already exists, except the `setup-*` claims of `claims.json` (below)
and the identity and phone claims it generated, when your contact answers
change.

| File | What it holds |
| --- | --- |
| `claims.json` | One identity claim, `name · city · email · LinkedIn` as you typed them, and a phone claim if you gave a phone. Its id is `identity_claim_id` from `profile.toml` (`identity-contact` by default). Then the `setup-*` claims of steps 5-8. The file has mode `0600` |
| `synonyms.json` | `{}` |
| `judge/facts.md` | Your target positions, level, work modes, work location, minimum salary, spoken languages (with levels) and main skills |
| `judge/anchors.md` | Only the "generated by /setup" marker |
| `judge/rubric_persona.md` | Judge rules for each family you chose (AI, Backend, or a generic text for your own titles) and for your level |

The judge files are written in the bot language you chose, and each one
starts with the marker `<!-- generated by /setup — edit to improve results -->`.
The setup then loads the profile with the strict loaders. If the profile does
not load (for example, an existing `claims.json` without the identity claim),
the setup removes the files it just created and shows the error.

### Claims from steps 5-8 (`setup-*`)

| Id | Category | Text (tags) |
| --- | --- | --- |
| `setup-lang-<code>` or `setup-lang-<slug>` | `language` | `<Language> — <level>`, for example `Spanish — Native` (the language name) |
| `setup-skill-<slug>` | `skill` | The skill as you typed it (the skill) |
| `setup-experience` | `experience` | The years answer, then `Last role: <role> at <company>` with only what you typed, for example `3-5 years of professional experience · Last role: Backend Developer at Acme` (role, company) |
| `setup-education-<slug>` | `education` | One claim per education entry, as you typed it (no tags) |

Language names, levels and the experience words are in the bot language you
chose. `<slug>` is the text in lowercase ASCII (`+` becomes `plus`, `#`
becomes `sharp`, anything else `-`): `C++` is `setup-skill-cplusplus`,
`Node.js` is `setup-skill-node-js`. A repeated id gets `-2`, `-3`, and a text
with no ASCII letter or digit uses its position. Skipped answers give no
claim.

The `setup-` prefix is reserved for these claims. When you run `/setup`
again, the claims whose id starts with `setup-` are replaced by the ones of
your new answers. Every other claim, the ones you wrote by hand included,
keeps its exact bytes; the new claims go after them. If the `setup-*` claims
do not change, the file is not rewritten. The merge is atomic, it is checked
with the strict loader, and it is part of Confirm's all-or-nothing undo.
To keep a claim of your own, give it an id without the `setup-` prefix.

Nothing you did not state reaches `claims.json`. The setup does not ask for
projects, so the claims have none yet; add them to `claims.json` by hand
for now (a later version reads them from your CV).

These files are not created, and the features that use them stay off until
you add them: `cover_letter_base.toml` (cover letters), `voice.md` (the voice
sample for letters), `salary_reference.toml` (salary estimates on cards),
and `honest_gaps.md`. Base CV delivery works for the CVs you uploaded in
step 13 (`cv_base_pdfs/`); skipped slots have no CV to send until you add one.

## For developers

- Code: `cva/onboarding/` (`flow.py` is the conversation, `answers.py` handles
  validation and the file mapping, `portals.py` loads the terms warnings from
  `extension/_locales`). Text: `cva/i18n/onboarding.py`.
- Step 13 (the CV upload) is `cva/onboarding/cv_upload.py` (slots,
  validation, pending area, manifest merge) with its `cv_pdfs` step in
  `flow.py`. The PDF arrives
  through `flow.on_document`, which the bot's document handlers call.
- `/setup <section>`: `flow.SECTION_STEPS` lists each section's steps and
  `flow.find_section` resolves names and aliases; `prefill.current_answers`
  reads the current values. Confirm passes the section to
  `answers.write_answers`, which applies only that section's keys, skips a
  TOML file whose document does not change, and records `section` in
  `onboarding_answers.json`; `profile_gen.confirm_hook` reads it and merges
  the `setup-*` claims only for the full run and `background`.
- The Confirm write is `commit.commit_all` (the answers, the base CVs and the
  confirm hooks, all or nothing). It holds `commit.WRITE_LOCK`, which the
  bridge's `POST /profile` shares, so a Telegram Confirm and a Profile-tab
  save never interleave.
- The extension's Profile tab (`extension/profile_editor.js`) talks to
  `GET /profile` and `POST /profile` on the bridge (`cva/bridge.py`), with the
  same `X-CVA-Bridge-Secret` header as the other endpoints; a request with a
  web page's `Origin` is refused (403), and setup mode answers 503.
  `profile_edit.profile_view` builds the GET answer (values per section,
  choices, limits, `setup-*` and other claims). A POST body is
  `{"section": <positions|level|background|location|salary|offer_langs>,
  "values": {...every field of that section}}`; `profile_edit.save`
  validates it with the `answers.clean_*` helpers and limits, merges it into
  `prefill.current_answers`, writes it like `/setup <section>` and reloads the
  preferences. A body that is not JSON (including one nested deeper than
  the decoder allows) answers 400 `{"error": "invalid json: ..."}` and
  writes nothing. A refused body answers 400 `{"error": "invalid fields",
  "fields": {<field>: <code>}}` (codes in `profile_edit.ERROR_CODES`); a
  failed write answers 500 with `detail` and `rolledBack`.
- The places of the suggested searches are derived in
  `cva/profile_prefs.py` (`_answer_locations`) when `preferences.toml` is
  read, only while `[search_targets] locations` is not written; the bridge
  serves them through `cva/suggest.py`. The next-step line of the full
  `/setup` reply is `onboarding.next_step`.
- The starter CVs are `cva/cvgen/starter.py` (fpdf2 and the CV font,
  templates `classic`, `modern`, `compact`) fed by
  `cva/onboarding/starter.py` (`starter_cv`, `create_pending_cv`, which runs
  the result through `cv_upload.accept_download`, and `write_cover_letter`).
  The Confirm reply has one extension point for an extra line and keyboard:
  `flow.CONFIRM_REPLY_EXTRAS` (callables `(env, lang)` returning a
  `flow.ConfirmExtra` or `None`).
- The profile endpoint's `education` is `null`, a list of entries, or one
  string split like the questionnaire answer; `GET /profile` shows the
  entries joined with `; `, which posts back unchanged.
- A button pressed again that leaves the message as it is (Telegram's
  "message is not modified") is ignored; any other edit error propagates.
- Callback data looks like `ob:<step>:<action>[:<arg>]`. The prefix `ob` is
  registered in `bot.CALLBACK_HANDLERS`. In setup mode, a
  `CallbackQueryHandler(pattern="^ob:")` handles these callbacks.
- Extension points: `flow.STEP_HOOKS` holds extra `Step` objects that are
  asked before the summary (CV upload). `flow.CONFIRM_HOOKS` holds callables
  `(profile_dir, answers)` that run after the files are written.
  `cva/onboarding/profile_gen.py` `confirm_hook` creates the minimal profile
  (templates in `cva/onboarding/templates/<lang>.toml`).
- Steps 5-8 are in `flow.py` (`spoken_langs`, `spoken_levels`, `skills`,
  `exp_years`, `last_role`, `last_company`, `education`; the typed languages
  go through the side step `spoken_other`). `answers.background` shapes their
  answers for `onboarding_answers.json`, and `profile_gen.setup_claims` /
  `profile_gen.merge_setup_claims` build and merge the `setup-*` claims.
