# Getting started

From a fresh clone to the first judged offer in Telegram. Allow about 20
minutes. Follow the steps in order: everything is set up first (steps 1 to
8), and how to use the bot day to day comes last (step 9). You do not write
profile files by hand: a short terminal wizard sets up the bot (step 4), and
the bot then asks about you in Telegram (step 6, about 10 minutes).

Don't let your coding assistant fill in your profile: use `/setup`. A coding
agent (Claude Code, Codex…) working in the clone reads `AGENTS.md`, which
tells it to leave `profiles/` to the bot.

## Before you start

Read these three points; they are also in the README.

- **Portals.** The extension reads pages in your own logged-in browser tab,
  at low volume, only for the portals you enable. Some portals (notably
  LinkedIn) prohibit automated access or browser extensions in their terms,
  and your account could be restricted. Every portal is off by default;
  enable only those whose terms you have read and accept, and never
  redistribute collected listings. You are responsible for compliance.
- **Claude Code.** lazyhunt does not use API keys by default and never logs
  you in to Claude. It runs the official, unmodified `claude` CLI that you
  installed and authenticated yourself, and never reads or stores its
  credentials. This is an independent project, not made, endorsed or
  sponsored by Anthropic. Check Anthropic's terms and your plan's usage
  limits.
- **Gmail** (optional) is read-only (`gmail.readonly`), through an OAuth
  client you create in your own Google Cloud project. Message bodies are
  never stored, tokens stay on your machine and nothing is sent to the
  author. Never commit `credentials.json` or the token file.

## 1. Prerequisites

| You need | Check |
| --- | --- |
| [uv](https://docs.astral.sh/uv/) (it downloads Python 3.12 for you) | `uv --version` |
| One AI provider: [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (default) or [Codex CLI](https://github.com/openai/codex), installed and logged in; or an API key for Anthropic, OpenAI or any OpenAI-compatible API (Gemini, OpenRouter, Groq, Mistral); or a local model with Ollama or LM Studio. Web research of company facts works only with the two CLIs (API models have no browsing tool). See [providers.md](providers.md) | `claude auth status` / `codex login status` |
| A Telegram account | |
| Chrome or another Chromium browser | |

lazyhunt runs on Linux, macOS and Windows (CI runs the tests on all three). To install uv,
use the official one-liner ([other methods](https://docs.astral.sh/uv/getting-started/installation/)),
then open a new terminal:

```bash
# Linux and macOS
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

`pyproject.toml` pins Python 3.12 (`>=3.12,<3.13`); `uv sync` downloads it if
your system does not have it.

## 2. Install

```bash
git clone <this repository> lazyhunt && cd lazyhunt
uv sync
```

Every command is `uv run lazyhunt <command>` (`uv run lazyhunt --help`
lists them). The older forms `uv run cli.py …` and `uv run bot.py` still
work.

## 3. Create your Telegram bot

Do this once, in the Telegram app, before the wizard:

1. Search for [@BotFather](https://t.me/BotFather) (blue check mark) and
   open it.
2. Send `/newbot`.
3. Type a name for your bot (any name, e.g. `My job hunter`).
4. Type a username that ends in `bot` (e.g. `myjobhunter_bot`).
5. BotFather replies with a token like `1234567890:AAH…`: copy the **whole**
   token and keep it private.

Already have a bot? In @BotFather send `/mybots`, pick your bot and tap
**API Token**.

## 4. Run the setup wizard

```bash
uv run lazyhunt init
```

The wizard only sets up the bot; everything about you comes later, in
Telegram. It asks everything first, shows a summary, and writes only after you
confirm. Press Enter to skip any step; you can run it again at any time and
it never overwrites a value without asking.

The first question is the language (asked in English and Spanish); the rest
of the wizard speaks the language you choose.

1. **Prerequisites.** It checks Python, `uv`, and `claude` (`claude --version`
   and `claude auth status`; only the "logged in" yes/no is read). If
   `claude` is missing or logged out it tells you how to fix it and goes on.
   It also says whether `codex` is installed (it is not run).
2. **Bot language.** `auto` (default: your system language, Spanish if it
   starts with `es`, English otherwise), `en` or `es` to force one: the
   language of the Telegram bot, the command line and the dashboard, saved as
   `ui_language`. CVs and
   letters keep following each offer's language. To change it later, edit
   `ui_language` in `config.local.toml` (or run the wizard again).
3. **AI provider.** `claude_code` (default), `codex_cli`, `anthropic` or
   `openai_compatible`, each with a one-line explanation (a number 1-4 also
   works).
   - `claude_code` and `codex_cli` use your own CLI login; no key is asked.
   - `anthropic` asks your API key (hidden input), saved as
     `ANTHROPIC_API_KEY` in `.env`.
   - `openai_compatible` asks an endpoint preset: OpenAI, Gemini, OpenRouter,
     Groq, Mistral, Ollama (local), LM Studio (local) or Other (type the base
     URL). Then the model, filled in from the preset (type it for a local
     server), and the API key (hidden), saved as `CVA_LLM_API_KEY`. A local
     server ignores the key, but one must be set: any non-empty value works,
     and Enter sets the placeholder `local`.

   On a re-run, Enter keeps every current value, including the key.
4. **Telegram.** Paste the whole token from step 3 (the input is hidden,
   nothing appears while you paste). The wizard asks again if the value is
   not a bot token (for example your AI key) and shows only its last 4
   characters. Your numeric user id is **not** asked: the bot
   pairs with you through a one-time code when it starts (step 5). If you
   already know it, `uv run lazyhunt init --owner-id <id>` writes it.
5. **Bridge secret.** A long random secret is generated for the extension.
   You never copy it: the bot gives the extension a one-time pairing code
   (`/connect`, step 7).
6. **Profile directory.** Default `profiles/me` (gitignored). Choose an
   empty profile (the default and the recommended choice: the bot starts in
   setup mode and builds the profile with you through `/setup`), a skeleton
   with placeholders to fill in by hand, or a copy of the fictional demo
   profile to try things first. An existing directory is never touched.
7. **Gmail.** Only a pointer to [gmail.md](gmail.md); it stays off.

Where things go:

| File | Contents |
| --- | --- |
| `.env` (mode 600) | `CVA_TELEGRAM_TOKEN`, `CVA_BRIDGE_SECRET`, and `ANTHROPIC_API_KEY` or `CVA_LLM_API_KEY` if your provider needs one |
| `config.local.toml` | `ui_language`, `llm_provider` (plus `llm_base_url` and `llm_model` for `openai_compatible`), `profile_dir`; `owner_telegram_id` once you pair with `/start <code>` |
| `profiles/me/` | your profile |

Both files are gitignored. Secrets are read from the environment only;
values put in TOML files are ignored. Manual setup is still possible: copy
`.env.example` and `config.example.toml` and edit them.

## 5. Start the bot and claim it

```bash
uv run lazyhunt bot
```

**The first time**, the terminal shows a one-time code such as
`/start ABCD-EFGH` (plus a `t.me` link that sends it for you). Send exactly
that to your bot: it answers that you are its owner and saves your id as
`owner_telegram_id` in `config.local.toml`. From then on it answers only you.
Until then it ignores every message and gives no answer to a wrong code. The
code works once, for 30 minutes; after 5 wrong tries it stops working, and a
restart prints a new one. It is shown only in a terminal, never in a log
file, so run `uv run lazyhunt bot` in a terminal the first time even if you later
run it as a service.

With an empty profile the bot is in **setup mode**: only `/help`, `/start`,
`/setup` and `/connect` work until the profile is complete, and `/start`
tells you to run `/setup`, the next step.

The bot runs as long as that terminal stays open. To keep it running in the
background or start it when you log in (systemd, launchd or Task Scheduler),
see [running.md](running.md).

## 6. Answer `/setup` in Telegram

Send `/setup` (`/configurar` in Spanish). The bot asks one question per
message, with buttons wherever the answer is a choice. It takes about 10
minutes:

1. the bot language;
2. your contact details: name, email, phone (optional), city and LinkedIn;
3. the positions you want: the built-in AI and Backend families, or your own
   job titles;
4. your level: junior, mid or senior;
5. the languages you speak and your level in each;
6. your main skills;
7. your years of experience, last role and company;
8. your highest education;
9. work modes (remote, hybrid, on-site), the city and the country you want
   to work in (the city's country is proposed as one tap);
10. a minimum gross annual salary (optional);
11. the languages an offer may be written in;
12. the portals to enable, each with its terms warning;
13. your base CVs as PDF files, one per position and language (you can skip
    them and add them later);
14. a summary, with **Edit** and **Confirm**.

Every step has **Back** and **Cancel**, and nothing is saved until you
press **Confirm**. Confirm is all or nothing: if anything fails, every file
is put back as it was, your uploads are kept, and you can press Confirm
again once the cause is fixed. It writes `preferences.toml`, `profile.toml`,
a minimal `claims.json` with only what you stated, the judge files and your
base CVs; it activates the portals you chose in `config.local.toml`; and the
bot switches to normal mode right after Confirm, with no restart. The reply ends with
the next step: suggesting searches in the extension (step 7). Every question,
file and limit: [onboarding.md](onboarding.md).

You can change any answer later (step 9).

For an advanced, hand-written profile (projects, base cover letter, voice
sample, your own role families), the [profiles README](../profiles/README.md)
shows the whole layout and the fictional `profiles/demo` the format of every
file; role families are in [role-families.md](role-families.md). To try the
bot before answering anything, choose the demo profile in the wizard (or set
`profile_dir = "profiles/demo"`).

## 7. Load the extension

1. Open `chrome://extensions`, turn on **Developer mode**, click **Load
   unpacked** and select the `extension/` directory.
2. Send `/connect` (`/conectar`) to the bot; it answers a one-time code.
   In the extension's **Options › Schedule and bot**, type it under **Pair
   with code** and press **Pair**: the bridge URL
   (`http://127.0.0.1:8765/bridge`) and secret are filled in and saved. The
   code works once, for 10 minutes. Typing the secret by hand still works
   (`CVA_BRIDGE_SECRET` in `.env`; the wizard can show it once at the end).
3. In **Options › Portals**, switch on the same portals you chose in
   `/setup`. `/setup` activated them on the bot, but a portal is scanned only
   when it is on in both places. Each switch shows the portal's terms warning
   and asks the browser for permission to that single domain.
4. In **Options › Searches**, press **Suggest from my profile**. It proposes
   searches built from your answers: the roles of each position you chose, in
   your city when you accept on-site or hybrid work, plus a remote search
   when you accept remote work. Tick the ones you want, or add your own.
   Until you have a search, the popup says "No searches yet" and points here.

## 8. Get your first offer

Click **Check now** in the extension's toolbar popup to run a round
immediately (when it cannot scan it says why at once: bot not reachable,
secret rejected, `/setup` not finished, no searches, or no portal enabled;
**Pause** stops a round in progress), or paste an offer link into Telegram with `/analyze <url>`.
After that, rounds run on their own every 60 minutes by default.

## 9. Day to day

Everything is set up; from here on, this is how you use lazyhunt. Each
command has a Spanish name too (shown with `ui_language = "es"`); both work.

- **Offer cards.** Each new offer arrives as a card with its score, verdict
  and buttons: **Generate CV** (switch **Language** and **Variant** first if
  you like), **Cover letter**, **Research salary**, **Mark as sent** and
  **Discard**. Under a CV that lazyhunt created in `/setup`, **Adapt to this
  offer** proposes small, reviewable changes. The
  [README](../README.md#8-day-to-day) explains every button.
- **Base CVs.** `/cvs` lists them and sends, re-templates or rebuilds them;
  `/setup cvs` adds the ones you skipped.
- **Tracking.** `/status` (`/estado`) lists your last 10 offers and
  `/status <id>` shows one; `/sent <id>` records an application;
  `/outcome <id>` records what happened next; `/panel` sends an offline
  dashboard.
- **Interviews.** `/prep <id>` builds a preparation sheet; `/star` keeps your
  STAR stories.
- **Pause.** `/pause` stops every automatic task at once and `/resume`
  restarts them; the extension popup has the same **Pause** button.
- **Change your profile.** `/setup <section>` (for example
  `/setup salary`) re-asks only that part, prefilled with your current
  answers, or use the extension's **Options › Profile** tab.

`/help` lists every command and button in the bot.

## Revoking access

- Telegram: revoke the token with BotFather (`/revoke`).
- Gmail: delete the token file and remove the app at
  [myaccount.google.com/permissions](https://myaccount.google.com/permissions).
- Portals: disable them in the extension's Options, which also drops the
  browser permission.
- Claude Code: lazyhunt stores nothing; `claude auth logout` logs the CLI
  out.

## Next

- [running.md](running.md): keep the bot running; the README has
  [Troubleshooting](../README.md#troubleshooting),
  [Updating](../README.md#updating) and
  [Uninstalling](../README.md#uninstalling).
- [architecture.md](architecture.md): how the pieces fit.
- [providers.md](providers.md), [portals.md](portals.md), [gmail.md](gmail.md).
- [adr/](adr/): why it is built this way.
