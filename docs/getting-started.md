# Getting started

From a fresh clone to the first judged offer in Telegram. Allow about 20
minutes. You do not write profile files by hand: a short terminal wizard
sets up the bot (step 3), and the bot then asks about you in Telegram
(step 5, about 10 minutes).

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

## 3. Run the setup wizard

```bash
uv run cli.py init
```

The wizard only sets up the bot; everything about you comes later, in
Telegram. It asks everything first, shows a summary, and writes only after you
confirm. Press Enter to skip any step; you can run it again at any time and
it never overwrites a value without asking.

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
4. **Telegram.** Create a bot: open [@BotFather](https://t.me/BotFather),
   send `/newbot` and follow the steps; paste the token it gives you (the
   input is hidden). Get your numeric user id from
   [@userinfobot](https://t.me/userinfobot); the bot answers only that id.
5. **Bridge secret.** A long random secret is generated for the extension.
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
| `config.local.toml` | `ui_language`, `llm_provider` (plus `llm_base_url` and `llm_model` for `openai_compatible`), `owner_telegram_id`, `profile_dir` |
| `profiles/me/` | your profile |

Both files are gitignored. Secrets are read from the environment only;
values put in TOML files are ignored. Manual setup is still possible: copy
`.env.example` and `config.example.toml` and edit them.

## 4. Start the bot

```bash
uv run bot.py
```

Send `/help` to your bot in Telegram. With an empty profile it answers in
**setup mode**: only `/help`, `/start` and `/setup` work until the profile
is complete, and `/start` tells you to run `/setup`.

The bot runs as long as that terminal stays open. To keep it running in the
background or start it when you log in (systemd, launchd or Task Scheduler),
see [running.md](running.md).

## 5. Answer `/setup` in Telegram

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
   to work in;
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
the next step: suggesting searches in the extension (step 6). Every question,
file and limit: [onboarding.md](onboarding.md).

**Change your profile later** with `/setup <section>` (for example
`/setup salary`), which re-asks only that part, prefilled with your current
answers, or in the extension's **Options › Profile** tab.

For an advanced, hand-written profile (projects, base cover letter, voice
sample, your own role families), the [profiles README](../profiles/README.md)
shows the whole layout and the fictional `profiles/demo` the format of every
file; role families are in [role-families.md](role-families.md). To try the
bot before answering anything, choose the demo profile in the wizard (or set
`profile_dir = "profiles/demo"`).

## 6. Load the extension

1. Open `chrome://extensions`, turn on **Developer mode**, click **Load
   unpacked** and select the `extension/` directory.
2. In the extension's **Options › Schedule and bot**, set the bridge URL to
   `http://127.0.0.1:8765/bridge` and paste the bridge secret. It is
   `CVA_BRIDGE_SECRET` in `.env`; the wizard can also show it once at the end.
3. In **Options › Portals**, switch on the same portals you chose in
   `/setup`. `/setup` activated them on the bot, but a portal is scanned only
   when it is on in both places. Each switch shows the portal's terms warning
   and asks the browser for permission to that single domain.
4. In **Options › Searches**, press **Suggest from my profile**. It proposes
   searches built from your answers: the roles of each position you chose, in
   your city when you accept on-site or hybrid work, plus a remote search
   when you accept remote work. Tick the ones you want, or add your own.

## 7. Get your first offer

Click **Check now** in the extension's toolbar popup to run a round
immediately, or paste an offer link into Telegram with `/analyze <url>`.
After that, rounds run on their own every 60 minutes by default. `/pause`
stops every automatic task at once (`/pausa` in Spanish; both work).

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
