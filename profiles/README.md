<h1 align="center">Profiles</h1>

<p align="center">
  <b>Everything that makes lazyhunt about <i>you</i> lives in one folder.</b><br>
  Your facts, your base CVs, your voice and your taste in offers.<br>
  No code to change, and nothing here ever leaves your machine except what you send to your AI provider.
</p>

<p align="center">
  <a href="#the-layout"><b>Layout</b></a>
  &nbsp;·&nbsp;
  <a href="#where-things-go"><b>Where things go</b></a>
  &nbsp;·&nbsp;
  <a href="#create-yours"><b>Create yours</b></a>
  &nbsp;·&nbsp;
  <a href="../docs/getting-started.md"><b>Setup guide</b></a>
</p>

Most people never write these files by hand: after `uv run cli.py init` with an empty profile, the bot's `/setup` questionnaire in Telegram writes them from your answers ([docs/onboarding.md](../docs/onboarding.md)). This page is for reading them, editing them by hand, and the advanced parts `/setup` does not ask about.

A profile is a plain directory that describes one job seeker. The bot reads it to judge offers honestly and to write CVs and letters that only use facts you have verified. This repository ships one complete, fictional example, [`demo/`](demo) (the invented candidate "Alex Rivera"), so you can see the format of every file before you write a line about yourself.

## The layout

```text
profiles/
├── demo/                         # the fictional example: copy its format, not its content
└── me/                           # yours, created by `uv run cli.py init` (never committed)
    ├── README.md                 # a checklist of what to replace
    ├── claims.json               # required · your verified facts
    ├── synonyms.json             # required · offer keywords mapped to your claims
    ├── profile.toml              # required · identity and file naming
    ├── preferences.toml          # required · gates, searches, cover-letter emphasis, role families
    ├── salary_reference.toml     # required · salary ranges used for the salary card
    ├── cover_letter_base.toml    # required · your base cover letter, per language
    ├── voice.md                  # required · a sample of how you write
    ├── honest_gaps.md            # required · what you do not have, and why that is fine
    ├── judge/
    │   ├── facts.md              # required · what the AI judge must know about you
    │   ├── anchors.md            # required · example offers with the score you would give
    │   └── rubric_persona.md     # required · how to weigh requirements for you
    ├── masters/                  # required · master CV content, one JSON per family and language
    │   └── AI_EN.json  AI_ES.json  BACKEND_EN.json  ...
    ├── cv_base_pdfs/             # required · your own base CVs
    │   ├── manifest.json         #            which PDF is which role family and language
    │   └── CV_<Role>_<You>.pdf   #            one per family and language
    └── eval/                     # optional · only for `uv run cli.py judge-eval`
        ├── README.md
        ├── labels.json           #            the scores you expect
        └── offers/               #            offers saved as text files
```

A configured profile is read as a whole: there is no per-file fallback, so a missing file fails loudly instead of silently mixing two people. Only `eval/` can be left out.

## Where things go

| You have | Put it in | Notes |
| --- | --- | --- |
| Your **base CV PDFs** | `cv_base_pdfs/` | List each one in `manifest.json` with its role family and language |
| Your **base cover letter** | `cover_letter_base.toml` | One table per language (`[es]`, `[en]`, `[ca]`); the model only writes the linking sentences |
| A **voice sample** | `voice.md` | A few paragraphs you wrote yourself; letters imitate this tone |
| The **judge files** | `judge/` | `facts.md`, `anchors.md` and `rubric_persona.md`: plain text, no code |
| Your **role families** (your own kinds of job) | `preferences.toml` `[families.<id>]` plus `cv_base_pdfs/manifest.json` | Frontend, marketing, nursing and so on: [docs/role-families.md](../docs/role-families.md) |
| The facts a CV may use | `claims.json`, `masters/` | A claim that is not in `claims.json` never appears in a CV or a letter |

## What you accept: `[search]` and `[location]`

`preferences.toml` also records what you told the first-run questionnaire. Every key is optional; leaving one out keeps the default, which changes nothing:

```toml
[location]
home_region = "catalonia"          # the detailed region pack (the only one shipped)
city = "Valencia"                  # where you live, free text; default: not set
country = "España"                 # your country by NAME, as offers write it; default: not set

[search]
work_modes = ["remote", "hybrid"]  # any of remote, hybrid, onsite; default: all three
level = "mid"                      # junior, mid or senior; default: not set
min_salary = 30000                 # gross per year, whole number; default: not set
currency = "EUR"                   # 3-letter ISO 4217 code for min_salary; default: EUR
```

City and country are matched by name against the offer's location, so write them the way offers do.

What the bot does with them:

- **Work mode and minimum salary never hide an offer.** When an offer's work mode is clearly one you did not list, or the salary it states tops out below `min_salary`, the offer still arrives and its card carries a ⚠️ line with the reason. Unclear work modes, offers without a salary, and a `currency` other than EUR (offer salaries are read in euros) give no warning.
- **Location.** With no `city` or `country`, or with a city inside `home_region` (Barcelona, Girona, Sant Cugat...), the detailed Catalonia gate applies as before. Any other city (or only a country) switches to a gate by name, with no distances: an offer in your city, or remote, passes; an on-site or hybrid offer in another city or country is flagged with ⚠️ "check" on the card, never discarded, because a name list cannot tell a suburb of your city from another city. With only `country`, an offer that names your country passes (for Spain, any Spanish place the bot knows too), and one in a place the bot knows abroad is flagged.

- **Suggested searches.** Without a hand-written `[search_targets] locations`, the extension's **Suggest from my profile** searches your `city` (or `country`) when `work_modes` includes `onsite` or `hybrid`, plus `remoto` (offers in Spanish or Catalan accepted, `[gates] allowed_languages`) or `remote` when it includes `remote`. A `locations` list you write wins; a profile with neither `work_modes` nor `city`/`country` keeps `["Barcelona", "remoto"]`.

Every key of `preferences.toml` is documented at the top of [`cva/profile_prefs.py`](../cva/profile_prefs.py).

When the bot saves your answers it validates the whole file first and rewrites it from scratch: hand-written comments are not kept, and the previous version is saved next to it as `preferences.toml.bak` (or `profile.toml.bak`).

## Create yours

```bash
uv run cli.py init
```

The setup wizard creates `profiles/me/`. Leave it **empty** (the default) and answer `/setup` in Telegram: it writes the profile from your answers. Choose the **skeleton** instead to write every file above by hand, each one filled with clearly marked placeholders, plus a checklist `README.md` that tells you what to replace. Prefer to explore first? Choose the demo copy in the wizard, or set `profile_dir = "profiles/demo"` in `config.local.toml`. An existing profile directory is never overwritten.

> [!IMPORTANT]
> `profiles/me/` is **ignored by git** and must never be committed: it holds your personal data. Only `profiles/demo/` and this README are tracked.

## Next steps

- [Getting started](../docs/getting-started.md): the whole setup, from clone to first scored offer.
- [Role families](../docs/role-families.md): add your own kinds of job, with their CVs, filters and thresholds.
