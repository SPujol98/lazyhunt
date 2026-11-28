# Role families: your own kinds of job

A **role family** is one kind of job you search for: "AI engineer",
"backend developer", "frontend developer", "nurse", "marketing manager".
Each family has its own CV variants, title filter, score threshold,
cover-letter emphasis and search keywords. You define them in one file of your
profile, with no code change.

If you declare nothing, LazyHunt keeps the two built-in families, `ai` and
`backend`, exactly as they always worked. Read on only if you want your own.

## What a family controls

| Part of LazyHunt | What the family decides |
| --- | --- |
| Routing | Which family an offer belongs to (by title words) |
| Title filter | Which titles the cheap pre-filter lets through |
| Score threshold | The score an offer needs to reach Telegram |
| CV | Which base CV files exist (language per family) and what they are called when delivered |
| Cover letter | The "branch emphasis" sentence the letter is steered by |
| Salary reference | Which rows of `salary_reference.toml` apply |
| Search suggestions | Which role searches the extension proposes |
| Telegram and CLI | The role buttons, `/cv` and `cli.py` choices |

## 1. The `[families.<id>]` tables

Families live in `<profile>/preferences.toml` (`profiles/me/preferences.toml`
after `cli.py init`). Each family is a table named after its **id**.

**Id rules.** Lowercase ASCII letters, digits and `_`, starting with a letter
(`frontend`, `data_science`, `nurse2`). At most **32 characters** (the id
travels inside Telegram buttons, which are limited to 64 bytes). Accents,
spaces, capitals and `-` are rejected with a message naming the id.

Every key is optional:

| Key | Meaning | Default |
| --- | --- | --- |
| `label` | Name shown on buttons, `/status` and the panel | the id |
| `languages` | CV languages this family has: any of `"es"`, `"en"`, `"ca"` | `["es", "en"]` |
| `title_terms` | Words that route an offer title to this family (whole words, ignoring case and accents) | none |
| `threshold` | Minimum score, an integer 0-100 | the global `threshold` in `config.local.toml` |
| `search_roles` | Role names the extension turns into searches | none |
| `cover_letter_emphasis` | One sentence of emphasis for letters of this family | see "Cover letters" |
| `default` | `true` on at most one family: the fallback | the first family you declare |
| `[families.<id>.title_filter]` | `enabled`, `include_groups`, `exclude_terms` (same as `[title_filters.ai]`) | see "Title filter" |

Unknown keys, wrong types, a threshold outside 0-100, an unknown language or
two families with `default = true` stop the profile from loading, with an
error that names the file, the table and the key.

Declaring any `[families.*]` table **replaces** the built-ins: if you still
want `ai` or `backend`, declare them too (a declared `ai` or `backend`
starts from its built-in values, so `[families.ai]` alone is enough).

## 2. Example: adding a `frontend` family

Say you search for frontend jobs only. Three steps.

**Step 1.** Add this to `<profile>/preferences.toml`:

```toml
[families.frontend]
label = "Frontend"
languages = ["es", "en"]
title_terms = ["Frontend", "Front-end", "React", "Vue", "Angular"]
threshold = 55
search_roles = ["Frontend Developer", "React Developer"]
cover_letter_emphasis = "FRONTEND branch: emphasise UI work (React, TypeScript, accessibility, performance), taking only the matching claims."
default = true

[families.frontend.title_filter]
include_groups = [["Frontend", "React", "Vue", "Angular"], ["Developer", "Engineer"]]
exclude_terms = ["Senior", "Lead"]
```

**Step 2.** Add one entry per CV language to
`<profile>/cv_base_pdfs/manifest.json`. The key is the family id in capitals,
an underscore and the language in capitals (`FRONTEND_EN`):

```json
{
  "variants": {
    "FRONTEND_ES": {
      "file": "CV_Frontend_ES.pdf",
      "sha256": "0",
      "delivery_name": "Frontend_Developer"
    },
    "FRONTEND_EN": {
      "file": "CV_Frontend_EN.pdf",
      "sha256": "0"
    }
  }
}
```

- `file` is the PDF's name inside `cv_base_pdfs/` (a plain `*.pdf` name).
- `sha256` is the PDF's SHA-256 hash. It is checked before every delivery, so
  it must match the file (step 3).
- `delivery_name` is optional: the role part of the file name you receive
  (`delivery_name` is slugged). Without it the label and language are used,
  for example `Frontend_EN`.

**Step 3.** Put the PDFs in `<profile>/cv_base_pdfs/` and write each file's
real hash into the manifest:

```bash
sha256sum profiles/me/cv_base_pdfs/CV_Frontend_ES.pdf profiles/me/cv_base_pdfs/CV_Frontend_EN.pdf
```

Copy each printed hash into the matching `sha256` field. Whenever you replace a
PDF, update its hash too: a PDF whose hash does not match is never sent.

A language without a manifest entry has no base CV for that family; asking
for it fails with a "no hay CV base para ..." error instead of sending
something else.

Restart the bot (and wait for its next round, or press Suggest in the
extension) and the family is live.

## 3. How an offer finds its family

1. The families are checked **in the order you declared them**. The first
   whose `title_terms` match the offer title wins.
2. If none matches, the extension's profile for the search (the "beacon"
   profile, a family id) is used.
3. If that is unknown or missing, the **default family** is used.

Offers that arrive by hand (a link or pasted text) are classified from their
title the same way: first matching `title_terms`, else the first family
**without** `title_terms` (a catch-all), else the default family.

Put your most specific families first and give one family no `title_terms`
if you want a catch-all.

## 4. Per-family settings

### Title filter

Each family has its own pre-filter. A family without a `title_filter`
table has **no include groups** (any title passes the include test) and only
the shared `company_blocklist` in `[title_filters]` as exclusion. Add
`include_groups` to narrow it. `exclude_terms` always gets the company
blocklist appended.

### Threshold

`threshold` is the score an offer needs for a card. Without it, the global
`threshold` from `config.local.toml` applies (the built-in `backend` family
keeps its own `backend_threshold`). Gray-zone width stays global.

### Cover letters

The emphasis sentence is picked in this order:

1. the family's `cover_letter_emphasis`;
2. `[cover_letter.branch_emphasis]` in `preferences.toml`, keyed by the CV
   role in capitals (`FRONTEND = "..."`, `AI = "..."`);
3. for `AI` and `BACKEND` only, the built-in default text;
4. the default family's emphasis (same order);
5. no emphasis line.

### Salary rows

In `salary_reference.toml`, `branch` is the CV role in capitals
(`branch = "FRONTEND"`). An offer of a family with no matching rows shows no
salary line (nothing is invented).

### Search suggestions

Suggest proposes, for each family, one search per `search_roles` entry and
location. A family without `search_roles` gets no suggested searches. The
extra "skill combination" searches built from your claims exist only for the
built-in `ai` and `backend` families.

## 5. Telegram

- Cards show a role button that cycles through your families in declared
  order, and a language button for the languages that family has.
- `/cv <id> FRONTEND en` builds a CV of that family and language. The role is
  the family id in capitals; `/help` lists your roles.
- `/status` marks offers of any family other than the default with its label
  in lowercase, for example `[frontend]`.
- **Old buttons keep working.** A button sent before you changed families
  that names a family you no longer have falls back to the default family and
  the bot says so ("la variante ... ya no esta en tu perfil"). Old
  Data Science buttons (`DS`) map to `AI`.

## 6. Command line

`cli.py` takes the same roles: the role choices of its CV command come from
your families (`FRONTEND`, `AI`, ...).

## 7. Browser extension

- The bot sends your family list to the extension on its next round (with
  `/config`) and whenever you press **Suggest**. The search profile selector
  and the title-filter editor then list your families.
- Each non-built-in family has its own title filter, starting from the one in
  `preferences.toml`, and your edits are stored separately. The built-in
  `ai` and `backend` filters are untouched.
- Importing a configuration file that mentions a family you do not have
  falls back to the default family and shows a notice listing the unknown
  ids. Nothing is lost.
- **Removing a family.** If you remove a family from your profile, the
  extension shows its searches and beacons under the default family. The next
  time you save in Options, they are stored as the default family
  permanently: declaring the family again later does not move them back, so
  reassign them by hand.
- **Importing on a fresh install.** Connect the bot first (set the bridge URL
  and secret, then let one round run or click **Suggest**) so the extension
  knows your families. Otherwise families it does not know fall back to the
  default family (the import shows a notice) and their per-family title
  filters are not imported.
- With no `[families]` table the extension looks exactly as before.

## 8. What stays built-in

- The **stack wall** (offers whose main language is not Python are dropped)
  applies to the built-in `backend` family only, not to custom families.
  `[stack_wall]` still tunes it.
- **Skill-combination searches** (see "Search suggestions").

## 9. Existing data and no families

- A profile with no `[families]` table keeps the built-in `ai` and `backend`
  families, their filters, thresholds, buttons and CV names exactly.
- Stored offers keep working: old `ai`/`backend` values and old CV names
  (including legacy `DS_*`) are still read. If you remove a family, its old
  offers show the default family's label.
- The database upgrades itself once on start-up (schema v23); existing data
  is unchanged.

## 10. Troubleshooting

| You see | Do this |
| --- | --- |
| `preferences.toml [families.X] invalid id` | Use lowercase ASCII, digits and `_`, starting with a letter |
| `id too long` | At most 32 characters |
| `only one default family is allowed` | Keep `default = true` on one family |
| "no hay CV base para FRONTEND_EN" | Add the manifest entry and the PDF (step 2 and 3) |
| A CV language is refused | The family's `languages` does not list it |
