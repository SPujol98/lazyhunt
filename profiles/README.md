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

## Create yours

```bash
uv run cli.py init
```

The setup wizard creates `profiles/me/` with every file above, each one filled with clearly marked placeholders, plus a checklist `README.md` that tells you what to replace. Prefer to explore first? Choose the demo copy in the wizard, or set `profile_dir = "profiles/demo"` in `config.local.toml`. An existing profile directory is never overwritten.

> [!IMPORTANT]
> `profiles/me/` is **ignored by git** and must never be committed: it holds your personal data. Only `profiles/demo/` and this README are tracked.

## Next steps

- [Getting started](../docs/getting-started.md): the whole setup, from clone to first scored offer.
- [Role families](../docs/role-families.md): add your own kinds of job, with their CVs, filters and thresholds.
