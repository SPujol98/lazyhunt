<p align="center">
  <img src="assets/readme/hero.svg" alt="lazyhunt. A relaxed sloth lies in a hammock at dusk, watching job offers with match scores drift past through binoculars. The job hunt that does the scrolling. You stay in the hammock." width="100%">
</p>

<p align="center">
  <a href="https://github.com/SPujol98/lazyhunt/actions/workflows/ci.yml"><img src="https://github.com/SPujol98/lazyhunt/actions/workflows/ci.yml/badge.svg" alt="CI status" height="20"></a>
  &nbsp;
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-F4A940?style=flat" alt="License: Apache-2.0" height="20"></a>
  &nbsp;
  <img src="https://img.shields.io/badge/python-3.12-4E8A55?style=flat" alt="Python 3.12" height="20">
  &nbsp;
  <img src="https://img.shields.io/badge/runs-locally-6B5A80?style=flat" alt="Runs locally, no server or account" height="20">
</p>

<p align="center">
  <b>A self-hosted job-search assistant that does the tedious scrolling for you.</b><br>
  It watches the job portals you choose from your own browser tab, scores every new offer<br>
  against a profile of facts you wrote, and drafts a tailored CV and cover letter, all from Telegram.<br>
  It never invents experience you don't have.
</p>

<p align="center">
  <a href="#quick-start"><b>Quick start</b></a>
  &nbsp;·&nbsp;
  <a href="#how-a-new-offer-flows"><b>How it works</b></a>
  &nbsp;·&nbsp;
  <a href="#telegram-commands"><b>Commands</b></a>
  &nbsp;·&nbsp;
  <a href="#customization"><b>Customization</b></a>
  &nbsp;·&nbsp;
  <a href="#privacy-and-security"><b>Privacy</b></a>
  &nbsp;·&nbsp;
  <a href="docs/getting-started.md"><b>Full setup guide</b></a>
</p>

> [!IMPORTANT]
> **Read this before enabling anything.**
> - **Portals.** The extension reads pages in your own logged-in browser tab, at low volume, only for the portals you enable. Some portals (notably LinkedIn) prohibit automated access or browser extensions in their terms, and your account could be restricted. Every portal is **off by default**: enable only those whose terms you have read and accept, and never redistribute collected listings. **You are responsible for compliance.**
> - **Claude Code.** lazyhunt never logs you in to Claude and never reads or stores Claude credentials. It runs the official, unmodified `claude` CLI that you installed and authenticated yourself. This is an independent project, not made, endorsed or sponsored by Anthropic; check Anthropic's terms and your plan's usage limits.
> - **Gmail** (optional) is read-only, through an OAuth client you create in your own Google Cloud project. Message bodies are never stored and nothing is sent to the author.
> - **Your responsibility.** lazyhunt is a personal tool provided as is, with no warranty (see the [license](LICENSE)). You choose what it searches, what it sends to your AI provider and what you send to employers; review every score, CV and letter before you use it.

## Why lazyhunt exists

I built lazyhunt to automate my own job search. Opening the same portals every day, scrolling the same lists and rewriting the same CV is exactly the kind of work a lazy person wants a machine to do, so I taught one to do it, with one rule it is not allowed to bend: it may only say about me what I can back up. It started as a tool for one person looking for work in Spain, and that still shows in a few places (see [limitations](#project-status-and-limitations)). Now it is yours to point at your own search.

## What this is

lazyhunt is two small pieces that live on your computer and talk to each other:

- **A browser extension** (Chrome, Brave or another Chromium-based browser) that, on a schedule, opens the saved searches you defined on the portals you enabled, in your own logged-in session, and picks out offers it has not seen before.
- **A Python bot** that receives those offers over a private, loopback-only connection, judges each one with the AI provider you configure, and talks to you through **Telegram**, the only interface.

There is no server, no account and no shared backend. Everything the bot knows about you comes from a **profile directory** of facts you wrote and approved.

**What you get on your phone:**

- A card for every new offer with a **0–100 score**, a verdict and the requirements you do and do not meet.
- One tap for a **tailored one-page CV** (Spanish, English or Catalan) and a **cover letter** built on your own base letter.
- **Tracking** of what you sent, follow-up reminders after 10 days of silence, an Excel export and an offline HTML dashboard.
- **Interview prep**: a bank of STAR stories from your profile and a preparation sheet per interview stage.
- A **kill switch**: `/pausa` stops everything automatic and survives restarts.

> [!NOTE]
> The Telegram interface is **in Spanish** today (commands such as `/pausa` and `/reanudar`, and all button labels). An English interface is planned. This README and the documentation are in English.

## Quick start

From a fresh clone to your first scored offer takes about 20 minutes, most of it spent writing your profile. Every step below is also in the longer [getting-started guide](docs/getting-started.md).

### What you need

| You need | Why | How to check |
| --- | --- | --- |
| **Python 3.12** and **[uv](https://docs.astral.sh/uv/)** | Runs the bot and installs its dependencies | `uv --version` |
| **Chrome or Brave** (other Chromium-based browsers should work, untested) | Hosts the extension; keep it open while scans run | |
| **A Telegram account** | The bot's only interface; you create your own bot with [@BotFather](https://t.me/BotFather) | |
| **One AI provider** | Scores offers and writes letters | see below |
| &nbsp;&nbsp;↳ [Claude Code](https://docs.anthropic.com/en/docs/claude-code) *(default)* | Your own installed and logged-in CLI; no API key needed | `claude auth status` |
| &nbsp;&nbsp;↳ or an Anthropic API key | Pay-per-use API | [docs/providers.md](docs/providers.md) |
| &nbsp;&nbsp;↳ or any OpenAI-compatible endpoint | A hosted or local model | [docs/providers.md](docs/providers.md) |
| *Optional:* a Google Cloud project | Read-only Gmail status proposals | [docs/gmail.md](docs/gmail.md) |
| *Optional:* Microsoft's Calibri font files | CVs render with the bundled, metric-compatible Carlito otherwise | see [limitations](#project-status-and-limitations) |

### 1. Install

```bash
git clone https://github.com/SPujol98/lazyhunt.git lazyhunt
cd lazyhunt
uv sync
```

`uv sync` creates a virtual environment and installs everything; you do not need to activate anything, just prefix commands with `uv run`.

### 2. Run the setup wizard

```bash
uv run cli.py init
```

The wizard asks everything first, shows you a summary, and **writes nothing until you confirm**. Press Enter to skip any step; it is safe to run again and never replaces a value without asking.

1. **Prerequisites.** Checks Python, `uv` and that `claude` is installed and logged in (only the yes/no is read). If something is missing it tells you how to fix it and carries on.
2. **AI provider.** `claude_code` by default. For `anthropic` or `openai_compatible`, you add the API key to `.env` yourself.
3. **Telegram.** Open [@BotFather](https://t.me/BotFather), send `/newbot` and follow the steps, then paste the token it gives you (the input is hidden). Get your numeric user id from [@userinfobot](https://t.me/userinfobot): the bot will answer **only** that id.
4. **Bridge secret.** Generates a long random secret that the extension uses to talk to the bot.
5. **Profile directory.** Creates `profiles/me` (gitignored), either as a skeleton with placeholders or as a copy of the fictional demo profile so you can try things first.
6. **Gmail.** Only a pointer to [docs/gmail.md](docs/gmail.md); it stays off.

Where things end up:

| File | Contents |
| --- | --- |
| `.env` (mode 600) | Secrets: `CVA_TELEGRAM_TOKEN`, `CVA_BRIDGE_SECRET` |
| `config.local.toml` | Settings: `llm_provider`, `owner_telegram_id`, `profile_dir` |
| `profiles/me/` | Your profile |

> [!TIP]
> Want to look around before writing anything about yourself? Pick the demo profile in the wizard (or set `profile_dir = "profiles/demo"`). It belongs to a fictional person and shows the format of every file.

### 3. Fill in your profile

Open `profiles/me/README.md` and work through its checklist. The essentials:

- **`claims.json`**: your verified facts. Only these can ever appear in a CV or a letter.
- **`profile.toml`** and **`preferences.toml`**: who you are and what you are looking for.
- **Your base CVs**, which lazyhunt adapts per offer.

### 4. Start the bot

```bash
uv run bot.py
```

Open your bot in Telegram and send `/help`. If it answers with the list of commands, the bot side is done.

### 5. Load the extension

1. Open `chrome://extensions` (Brave: `brave://extensions`), turn on **Developer mode**, click **Load unpacked** and select the `extension/` folder of this repository. The extension is not in a store; you load it straight from the code you can read.
2. Open the extension's **Options › Schedule and bot**. Set the bridge URL to `http://127.0.0.1:8765/bridge` and paste the bridge secret (it is `CVA_BRIDGE_SECRET` in `.env`; the wizard can also show it once at the end).
3. In **Options › Portals**, enable only the portals whose terms you accept. Each one shows its terms warning and asks the browser for permission to that single site.
4. In **Options › Searches**, add a search (keywords and location). Each search runs on every portal you tick, and **Suggest from my profile** can propose some for you.

Supported portals today: LinkedIn, InfoJobs, Indeed, Tecnoempleo, JobFluent, Manfred, Adzuna and foorilla.

<p align="center">
  <img src="assets/readme/screenshots/extension-options.png" alt="The extension's Options page: portal switches with InfoJobs, Indeed and Tecnoempleo enabled, and a search named AI Engineer Barcelona with its keywords, location, filter profile and portals." width="100%"><br>
  <sub>Options: enable portals, then add searches. (Demo data.)</sub>
</p>

### 6. Get your first offer

You do not have to wait for the schedule:

- Click **Check now** in the extension's toolbar popup to run a round immediately, or
- paste any offer link into Telegram with `/analyze <url>`.

<p align="center">
  <img src="assets/readme/screenshots/extension-popup.png" alt="The extension's toolbar popup: status Active, last round 12 minutes ago, six searches being watched with the jobs found on each, and the Check now, Pause and Options buttons." width="300"><br>
  <sub>The toolbar popup: what is being watched and a <b>Check now</b> button. (Demo data.)</sub>
</p>

A card arrives with the score, the verdict, what you match and what you do not, and buttons for the next steps. After that, rounds run on their own every **60 minutes** by default (adjustable in **Options › Schedule and bot**; the extension warns below 30).

### 7. Generate a CV and a cover letter

On an offer card:

- **📄 Generar CV** builds a one-page PDF. Switch the language (ES/EN/CA) and the variant on the card first if you like, or type `/cv 42 BACKEND en`.
- **✉️ Carta de presentación** keeps your approved base letter word for word and writes only the parts that change per offer (why this company, why this role). It researches the company on the web and shows every fact with its source URL, so you can check it. Details: [docs/cover_letters.md](docs/cover_letters.md).
- **✅ Marcar enviada** (or `/sent 42`) records that you applied.

### 8. Day to day

- Let rounds run in the background; review the cards that arrive.
- When a company answers, use `/outcome <id>` to record progress. Interviews and rejections you record can become **calibration examples** (`/calibracion`), so the score learns what actually works for you.
- Before an interview, `/prep <id>` gives you a preparation sheet and `/star` keeps your STAR stories.
- `/panel` sends an offline HTML dashboard of everything so far.
- Going on holiday? `/pausa` stops scans, automatic analysis and Gmail; `/reanudar` resumes. The extension popup has the same **Pause** button.

## Telegram commands

Send `/help` at any time for the full list with an example of each. The ones you will use most:

| Command | What it does |
| --- | --- |
| `/analyze [url]` | Score an offer from a link or pasted text |
| `/cv <id> [AI\|BACKEND] [es\|en\|ca]` | Generate the tailored CV PDF |
| `/status [id]` | The last 10 offers, or one offer's status |
| `/sent <id>` · `/outcome <id>` | Record an application and how it is going |
| `/panel` | Offline HTML dashboard with metrics, charts and a filterable table |
| `/prep <id>` · `/star` | Interview preparation and your STAR story bank |
| `/pausa` · `/reanudar` | Pause or resume every automatic task |

<details>
<summary><b>Full command reference</b></summary>

<br>

| Group | Command | What it does |
| --- | --- | --- |
| Analyze | `/analyze [url]` | Scores an offer against your profile. Without a link, it asks for the text. |
| | `/rescatar <id>` | Re-sends the full card of an offer that was discarded, without touching its score. |
| CVs and letters | `/cv <id> [AI\|BACKEND] [es\|en\|ca]` | CV PDF for offer `#id`. Role and language are optional and chosen automatically when missing. |
| | `/cartabase [es\|en\|ca]` (or `/base`) | Your generic letter for portals, with no specific offer, ready to copy. |
| Tracking | `/status [id]` | Without an id, the last 10 offers; with an id, that offer's status. |
| | `/sent <id>` | Records that you sent the application for offer `#id`. |
| | `/outcome <id>` | History of a sent application plus buttons to record progress. After 10 days without news it proposes a follow-up, which you send yourself. |
| | `/calibracion` | The real outcomes that calibrate the score; each one can be turned off. |
| | `/skip` | Skips the feedback note the bot asks for after an outcome. |
| | `/export` | Excel file with only the applications you sent. |
| | `/panel` | Offline HTML dashboard: metrics, charts, alerts, a filterable table and discarded offers. |
| Beacons and searches | `/keywords [profile]` | The keywords seen in offers and how often, optionally filtered by profile. |
| | `/beacons` | Every watched search ("beacon") with its status and last check. |
| | `/beacon resume\|disable <id>` | Resumes a paused beacon or disables it. |
| Interviews | `/star` | Builds your STAR stories (situation, task, action, result) and lists them to view, edit or delete. |
| | `/prep <id>` | Pick a stage (HR, technical, final) and get an HTML sheet with topics, gaps, STAR stories, salary and questions. |
| Other | `/pausa` | Stops everything automatic; `/analyze` keeps working. |
| | `/reanudar` | Turns back on what was paused. |
| | `/help` (or `/start`) | Commands and buttons. |

Card buttons include **Analizar**, **Generar CV**, language and variant toggles, **Investigar salario** (looks for a salary range on the web, with sources), **Carta de presentación**, **Descartar**, **Marcar enviada**, **Rehacer CV** and **Resultado**. `/help` explains every one of them.

The same service is scriptable from the command line:

```bash
uv run cli.py --help
uv run cli.py analyze <id>
uv run cli.py judge-eval      # measure the judge against labelled offers
```

</details>

## How a new offer flows

<p align="center">
  <img src="assets/readme/flow.svg" alt="How a new offer flows, in five steps: 1, the extension reads your saved searches in your own browser tab; 2, offers cross a loopback bridge on 127.0.0.1 protected by a shared secret; 3, deterministic gates and the AI judge score them against your verified claims; 4, a card arrives in Telegram; 5, a CV and cover letter are built only from facts in your profile." width="100%">
</p>

1. **Your tab, your pace.** On each round the extension opens your saved searches on the portals you enabled, in your own logged-in session, at low volume, and keeps the offers it has not delivered before.
2. **A private hand-off.** New offers are sent to the bot over a bridge that listens on `127.0.0.1` only and rejects anything without the shared secret. With no secret set, no port is opened at all.
3. **Judged against you, not against hype.** The bot stores each offer in a local SQLite database, extracts its requirements with your AI provider, runs deterministic gates (location, language, eligibility, and "walls": requirements you cannot back), and asks the AI judge for a 0–100 score and verdict against your claims.
4. **A card in Telegram.** You get the score, the verdict, what matches and what does not, and buttons for what to do next.
5. **Documents only from your facts.** CVs and letters are rendered locally from your own base CVs and letter. A claim that is not in your profile never appears in them.

<details>
<summary><b>What makes it different</b></summary>

<br>

- **It reads in your own tab.** No scraping server, no shared accounts and no stored portal passwords: the extension sees what you would see when you are logged in, only on the portals you switched on. Why: [ADR 0001](docs/adr/0001-browser-extension-in-the-users-tab.md), [ADR 0005](docs/adr/0005-portals-opt-in-with-terms-warnings.md).
- **Honest by design.** Requirements you cannot honestly claim stay unmatched. The CV and letter engines may only use facts from your profile, and company facts in a letter come with their source URL.
- **Hard gates before soft judgment.** Location, language, eligibility and walls are checked by deterministic code, so an AI score cannot talk its way past them.
- **It learns from what happened, not from guesses.** Interviews and rejections you record become approved calibration examples for the judge.
- **Your own AI, your own bill.** By default it uses the Claude Code CLI you already pay for and never forwards `ANTHROPIC_API_KEY` to it, so you are not silently switched to API billing. Why: [ADR 0002](docs/adr/0002-users-own-claude-code-cli-as-default-provider.md).
- **Your data stays a file.** One SQLite database on your disk ([ADR 0003](docs/adr/0003-sqlite-local-storage.md)); Gmail, if you want it, goes through your own OAuth client ([ADR 0004](docs/adr/0004-bring-your-own-google-oauth-client.md)).
- **One big off switch.** `/pausa` stops scans, automatic analysis and Gmail at once and stays off across restarts.

The full picture, component by component: [docs/architecture.md](docs/architecture.md).

</details>

## Configuration

Settings are layered: code defaults, then `config.toml`, then `config.local.toml` (personal, gitignored), then `CVA_*` environment variables. Secrets are read from the environment only. [`config.example.toml`](config.example.toml) documents the safe public defaults; to configure by hand instead of using the wizard, copy it to `config.local.toml` and `.env.example` to `.env`.

| Setting | Default | What it does |
| --- | --- | --- |
| `llm_provider` | `claude_code` | `claude_code`, `anthropic` or `openai_compatible` ([docs/providers.md](docs/providers.md)) |
| `profile_dir` | unset | Directory with your claims, base CVs, voice and preferences |
| `enabled_portals` | unset | Restrict which portals the bot accepts; unset lets the extension decide |
| `match_mode` | `keyword` | `hybrid` lets the AI judge drive notifications |
| `threshold` | `65` | Minimum score for the top-choice badge |
| `gmail_enabled` | `false` | Optional read-only Gmail sync ([docs/gmail.md](docs/gmail.md)) |
| `paused` | `false` | Initial state of the kill switch until `/pausa` or `/reanudar` is used |

## Customization

Everything that describes you or your search is yours to change, and none of it needs code. `<profile>` is your profile directory (`profiles/me` after `cli.py init`); the fictional [`profiles/demo`](profiles/demo) shows every file's format.

| To change | Where | Notes |
| --- | --- | --- |
| Searches: keywords, location, portals | Extension › Options › Searches | One search runs on every portal you enabled |
| Title filters (include / exclude) | Extension › Options › Title filters | A cheap pre-filter before the AI sees an offer; can be turned off per search |
| How the AI judges offers for you | `<profile>/judge/facts.md`, `anchors.md`, `rubric_persona.md` | Plain text: facts about you, a few example offers with the score you would give, and how to weigh requirements. `cli.py init` creates them with instructions |
| Your experience (the only source for CVs and letters) | `<profile>/claims.json`, `<profile>/masters/`, your base CVs | A claim that is not here never appears in a CV or a letter |
| Cover-letter voice and emphasis | `<profile>/voice.md`, `<profile>/preferences.toml` `[cover_letter]` | |
| AI provider and model | `llm_provider` in `config.local.toml`; the API key in `.env` | Your own Claude Code login (default, no key), an Anthropic API key, or any OpenAI-compatible endpoint: [docs/providers.md](docs/providers.md) |
| Score threshold, match mode, accepted portals | `config.local.toml` | See [Configuration](#configuration) |
| A portal that is not supported yet | Ask a coding agent | Open `claude` in the repository and say "add portal &lt;name&gt;" with a saved results page: the [`add-portal`](.claude/skills/add-portal/SKILL.md) skill scaffolds the adapter and its tests with `scripts/new_portal.py`. By hand: [docs/portals.md](docs/portals.md) |

## Tips for better results

- **Write claims you can defend in an interview.** The judge and the CV engine can only be as good as `claims.json`; vague claims give vague scores.
- **Spend ten minutes on `judge/anchors.md`.** A few real offers with the score *you* would give them teach the judge your taste faster than anything else.
- **Record outcomes.** Every interview or rejection you log with `/outcome` is a chance to calibrate the score.
- **Start with one or two portals** and a couple of searches, check the cards for a few days, then widen.
- **Read every CV and letter before sending it.** lazyhunt drafts; you decide.

## Privacy and security

- **Local first.** Offers, scores and history live in a local SQLite database. The bridge listens on `127.0.0.1` only and rejects requests without the shared secret.
- **What leaves your machine:** offer text and the relevant parts of your profile go to the AI provider you configured; card text goes to Telegram; company research uses public web pages; Gmail is read only if you enable it.
- **Only you can talk to the bot.** It answers your numeric Telegram id and nobody else, over long polling (no webhook, no public endpoint).
- **Secrets** (`CVA_TELEGRAM_TOKEN`, `CVA_BRIDGE_SECRET`, API keys) are read from the environment only; values placed in TOML files are ignored.
- **No accidental API billing.** The `claude` child process does not inherit `ANTHROPIC_API_KEY` unless you set `claude_code_pass_api_key = true`.
- **Revoking access:** delete the Gmail token file and remove the app at [myaccount.google.com/permissions](https://myaccount.google.com/permissions); revoke the Telegram token with BotFather (`/revoke`); disable portals in the extension's Options, which also drops the browser permission; `claude auth logout` logs the CLI out (lazyhunt stores nothing of it).

Found a security problem? Please follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## Project status and limitations

lazyhunt is an early release (`v0.1.0`, see the [changelog](CHANGELOG.md)) and was built first for one person's job search in Spain.

- The Telegram interface is in Spanish today; English is planned.
- Location rules ship for Catalonia and Spain; other regions need their own data.
- CV variants are fixed to three role families (AI, Data Science, Backend Python; Spanish and English, plus Catalan for AI) until the next release, which plans user-defined role families.
- Portal adapters depend on each site's page structure and can break when a site changes.
- AI scores are not deterministic; the gates and the calibration set keep them consistent, not exact.
- The setup wizard (`cli.py init`) covers the first configuration; finer settings are edited by hand (see [Configuration](#configuration)).
- The extension is loaded unpacked from this repository; it is not published in a browser store.
- The CV engine renders with Carlito (SIL OFL 1.1, metric-compatible with Calibri), shipped in `cvgen/fonts/carlito/`. If you own Microsoft's Calibri, put `calibri.ttf`, `calibrib.ttf`, `calibrii.ttf` and `calibriz.ttf` in `cvgen/fonts/` and set `cv_font = "calibri"`; Calibri is never redistributed.

<details>
<summary><b>Repository layout</b></summary>

<br>

```text
lazyhunt/
├── bot.py                 # Telegram bot: the entry point you run
├── cli.py                 # Same service from the command line, plus the `init` wizard
├── cva/                   # The engine
│   ├── bridge.py          #   loopback endpoint the extension talks to
│   ├── service.py         #   JobService, the seam every entry point uses
│   ├── match/             #   extraction, gates, AI judge, cover letters
│   ├── cvgen/             #   CV rendering
│   ├── tracker/           #   SQLite storage, Excel export
│   ├── gmail/             #   optional read-only Gmail sync
│   ├── commands.py        #   Telegram commands and /help text
│   └── llm.py             #   AI provider seam (Claude Code, Anthropic, OpenAI-compatible)
├── extension/             # Chromium (Manifest V3) extension (load unpacked)
│   ├── core/              #   rounds, scheduling, delivery
│   └── portals/           #   one adapter per portal
├── profiles/demo/         # Fictional example profile: the format reference
├── cvgen/fonts/carlito/   # Bundled CV font
├── docs/                  # Guides, architecture, decision records (adr/)
├── tests/                 # Test suite (no network needed)
└── config.example.toml    # Documented public defaults
```

</details>

## Development

```bash
uv run pytest -q                                   # test suite, no network needed
uv run mypy --strict cva                           # type checking
uv run ruff check cva tests bot.py cli.py scripts  # lint
```

Tests use fake AI clients and temporary directories; none of them call a real provider, portal or Telegram.

Design notes: [docs/architecture.md](docs/architecture.md) (components and data flow) and [docs/adr/](docs/adr/) (decision records: browser extension, Claude Code CLI, SQLite, bring-your-own OAuth client, opt-in portals). Adding a portal adapter is described in [docs/portals.md](docs/portals.md).

## Contributing

Issues and pull requests are welcome; [CONTRIBUTING.md](CONTRIBUTING.md) has the details. Please open an issue first for larger changes, keep each pull request focused, and include tests: the suite, `mypy --strict` and `ruff` must pass. Never commit personal data, saved portal pages, tokens or credentials.

## Acknowledgements

- [ai-job-search](https://github.com/MadsLorentzen/ai-job-search) by Mads Lorentzen for the portal-adapter and profile-driven search ideas (MIT).
- [linkedin_beacons](https://github.com/Edugs94/linkedin_beacons) by Edugs94, the browser extension the first versions of `extension/` were built on; version 2.0 is a clean-room rewrite (see [extension/PROVENANCE.md](extension/PROVENANCE.md)).
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot), [pydantic](https://github.com/pydantic/pydantic), [fpdf2](https://github.com/py-pdf/fpdf2), [httpx](https://github.com/encode/httpx) and [uv](https://github.com/astral-sh/uv).

## License

[Apache-2.0](LICENSE) © lazyhunt contributors.

<p align="center">
  <img src="assets/readme/mascot.svg" alt="The lazyhunt sloth hangs from a branch by one arm, holding a small card with a green check mark. Go stretch your legs. The next round runs without you." width="440">
</p>
