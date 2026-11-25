# Guided setup in Telegram (`/setup`, `/configurar`)

The guided setup asks for your profile in Telegram, in about 10 minutes, so
you never have to write the profile files by hand. It runs on the first start
(setup mode, after `uv run lazyhunt init` with an empty profile), and you can
run it again later, whole or one section at a time, to change your answers.
Where it fits in the whole first run: [getting-started.md](getting-started.md).

## How it works

- You get one question per message. Use the buttons when the answer is a
  choice, and type the answer when it is text. Each question starts with
  `Setup n/19` (`n/total` of a section with `/setup <section>`): the total
  never changes, a follow-up (each language's level, the company, the
  currency) keeps the number of the question it follows, and when no base
  CV is asked the summary comes right after the offer languages.
- `/setup` does not ask for portals: you enable a portal only in the
  extension (**Options › Portals**, after its terms warning) and then tick
  it in each search that should run on it.
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
bot**) and a profile that is already set up; contact data and base CVs stay
in `/setup <section>` (the base CVs are listed read-only at the end; `/cvs`
in the bot changes their template or rebuilds them), and portals are
enabled in **Options › Portals**.

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
12. Base CVs: one per position type × language. For each one, upload
    your PDF, create one with lazyhunt from your answers, or leave it for
    later; optionally a base cover letter (see below)
13. Summary, then Confirm or Edit

## After Confirm

A Confirm of the whole questionnaire, in one all-or-nothing step:

1. writes `preferences.toml` and `profile.toml` ([Where the answers go](#where-the-answers-go));
2. stores your base CV PDFs and their manifest ([Base CV PDFs](#base-cv-pdfs-step-12));
3. creates the files of the [minimal profile](#the-minimal-profile) that
   are still missing (`claims.json`, `synonyms.json`, the judge files).

It never writes `enabled_portals`: portals are enabled only in the extension.

If any of these fails, every file is put back as it was, your uploads stay,
and the reply says "Nothing was changed": fix the cause it names and press
**Confirm** again, with no need to answer or upload anything twice. If
undoing fails too (rare: a full disk or a permission change mid-write), the
reply says which file to check; each rewritten TOML file keeps its previous
version as `<name>.bak`.

When it succeeds, the bot loads the new profile at once: in setup mode it
switches to normal mode right after Confirm, with no restart and without
waiting for another message (the bridge then serves your suggested
searches). The reply ends with the next step in the extension: switch on the
portals you want in **Options › Portals** (each shows its terms warning and
asks the browser for permission to that one site), then **Options › Searches
› Suggest from my profile**, and tick in each search the portals it runs on.
A `/setup <section>` reply has no next step.

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
- **Portals:** LinkedIn and InfoJobs, plus the other registered portals
  that opt in to suggestions (narrowed to `enabled_portals` when you set
  that list by hand); you then tick, in each search, the portals it runs on.

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
| Base CV PDFs | `cv_base_pdfs/<name>.pdf` and `cv_base_pdfs/manifest.json` (see [Base CV PDFs](#base-cv-pdfs-step-12)) |
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

In the full bot, the new preferences take effect immediately
(`reload_preferences`).
Some service texts change only after a restart.

## Base CV PDFs (step 12)

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

### What a created CV is made of (`<SLOT>.source.json`)

A CV lazyhunt creates is also kept as its structured source,
`cv_base_pdfs/<SLOT>.source.json` (for example `AI_ES.source.json`), next to
the PDF: the slot, template and language, the header (name, target position,
contact line) and the sections (profile, experience, education, skills,
languages) with their items in order. Every item names the claim it says:
the `setup-*` claims of your answers (below), or the identity claim for the
header and, without an experience answer, for the profile line. The PDF is
drawn from this source (the same bytes as before for the same content), and
the source records the `sha256` of that PDF. Confirm writes it atomically
together with the PDF and the manifest (a failed Confirm undoes it too); the
manifest format is unchanged. Uploading a PDF over a created one removes its
source. A source no longer describes the slot's CV (and is ignored) when the
manifest's PDF is a different one or a claim it names is gone. Uploaded and
hand-made PDFs have no source.

### Your base CVs: `/cvs`

`/cvs` (both languages; not in `/help`, which is near Telegram's size limit;
the Confirm reply points to it) lists every base CV in one message: position
× language and where it comes from.

- **created with lazyhunt · <template> template**, with its sections and
  how many items each has (for example `Profile 1 · Experience 1 · Skills 5`).
  When an answer changed since it was made and a claim it used is gone, it
  says so: 🔄 rebuilds it.
- **uploaded PDF · not adaptable**, in a profile that also has created CVs;
  `/setup cvs` can create one with lazyhunt instead.
- **your own PDF** when no base CV has a source (hand-made PDFs, the legacy
  layout): listed plainly, nothing else changes.

Buttons per CV: **📄** sends the stored PDF; for created CVs only, **🎨**
redraws it from its source with another template and **🔄** rebuilds it from
your current answers (same template). Both replace the PDF, its source and
the manifest `sha256` all or nothing (a failure puts the three files back).
**Options › Profile** lists the same base CVs read-only (`base_cvs` in
`GET /profile`: slot, label, language, origin, template, number of sections).

### Adapting a created CV to an offer

After **📄 Generate CV** sends the base PDF, the card shows one more button,
**✨ Adapt to this offer** (*Adaptar a esta oferta*), only when that base CV
was created with lazyhunt and its source is current (see above). Uploaded
PDFs, hand-made PDFs and the legacy layout never show it: there the card is
exactly as before.

What adaptation can do, starting from the base CV and its claims:

- reorder the items of a section so the most relevant come first;
- feature up to 3 of the skills already on the CV in the headline;
- swap up to 5 words for the offer's own wording, only when the offer is in
  the CV language, the old word is in that line of your CV and the new word
  is in the offer. Only plain words are swapped: Latin letters (accents,
  ñ, ç, ü included), spaces, hyphens and apostrophes; never digits,
  symbols, other alphabets or hidden characters, and the word written in
  the CV is the offer's own spelling. The old word is never the role or
  the company (the bold lead of an experience line), a name, a date or a
  number word. When the new word is already an approved synonym
  (`synonyms.json`) or a tag of the same claim, the swap is simply applied.
  Otherwise it is a **new synonym** that you decide on (at most 3 per CV).

**New synonyms (you approve them).** The preview marks them
`🆕 new synonym: «disciplina» → «aguante»` (*nuevo sinónimo*). They are only
applied if you press **✅ Use this CV**; then each one is saved in your
profile's `synonyms.json` as an alias of that claim, so next time it applies
on its own, and it also improves offer matching: an offer asking for
"aguante" counts as partly covered (`~`, saved as a `weak` alias: an approved
wording, not proof of an exact requirement). **↩️ Remove change N** or
**Discard** saves nothing.

New synonyms are only offered for **soft skills and language-level
wording**: both the old and the new word must be in the bundled lexicon
(`cva/cvgen/soft_lexicon.json`, Spanish and English) in the same group of
equivalent words, for example «disciplina» / «constancia» / «aguante» /
«stamina», «trabajo en equipo» / «colaboración» / «teamwork», or
«nativo» / «lengua materna» / «mother tongue». A language level is only
swapped in the languages section and only for a word of the same level
(«nativo» never becomes «avanzado»). Tools, technologies, companies,
numbers, years, job titles and degrees are never in the lexicon, so they
are never proposed; on top of that a new synonym is still refused when it
belongs to another item of your profile, names a technology (including
the offer's analyzed tool/skill/domain requirements and the company) or
the offer only writes it capitalized. Words that are already approved in
your `synonyms.json` keep working as before (with the same plain-word and
old-word rules).

Re-running `/setup` never wipes your approved synonyms: when you remove a
skill, only the synonyms of that skill are dropped from `synonyms.json`
(and unusable keys, such as ones with hidden characters); the rest stay.

What it cannot do: add skills, tools, years, companies, titles or
achievements, or change the language of the CV. One LLM call (the judge
model) proposes the changes; deterministic checks keep only the provable
ones, and a final check refuses the whole CV if any word is not grounded in
your claims, the base CV, approved synonyms or, only in its own line, a new
synonym you are approving.

The preview lists the changes, numbered, and how many suggestions were
dropped. **✅ Use this CV** renders the adapted PDF to a separate file next
to the base copy (`<base name>_adapted_<id>.pdf`) and sends it; the offer
records it with an `ADAPTED:<sha>:<changes>` marker, so **Mark as sent** and
the CV mirror move that file, while **🔄 Redo CV** delivers the base again.
**↩️ Remove change N** drops one change, **Discard** drops the proposal
(the base CV stays). If the adapted CV would have more pages than the base,
the headline skills and then the reorders are dropped; if it is still
longer, nothing is made. A proposal lives in memory for 24 hours (a restart
forgets it). If the LLM fails, the base CV is untouched.

## Notes and limits

- The setup needs a profile directory (`profile_dir` in `config.local.toml`).
  If you use the legacy layout, which has no `profile_dir`, `/setup` tells
  you to run `uv run cli.py init` first, and `/help` and the command menu
  do not offer `/setup` there.
- `/setup` never asks for portals nor writes `enabled_portals` (a portals
  step there meant enabling a portal three times: in `/setup`, in the
  extension and in each search). An `enabled_portals` list an older
  `/setup` wrote to `config.local.toml` is removed once, on the next start
  of the bot: only that key's lines go (every other line and comment keeps
  its bytes; a symlinked file is written through its link), the log says
  so in one line, and the `app_state` key `portals_config_migrated`
  records it. A list you set by hand afterwards is kept and the bridge
  enforces it (see `enabled_portals` in the README's Configuration). If the
  file cannot be written, the bot logs a warning, keeps the old list and
  tries again at the next start.
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
step 12 (`cv_base_pdfs/`); skipped slots have no CV to send until you add one.

## For developers

- Code: `cva/onboarding/` (`flow.py` is the conversation, `answers.py` handles
  validation and the file mapping, `portals.py` removes the `enabled_portals`
  list of an older `/setup` once, through `JobService.migrate_portals_config`).
  Text: `cva/i18n/onboarding.py`.
- Step 12 (the CV upload) is `cva/onboarding/cv_upload.py` (slots,
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
