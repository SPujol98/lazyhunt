# Getting started

From a fresh clone to the first judged offer in Telegram. Allow about 20
minutes, most of it spent writing your profile.

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
| Python 3.12+ and [uv](https://docs.astral.sh/uv/) | `uv --version` |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code), logged in (or another provider, see [providers.md](providers.md)) | `claude auth status` |
| A Telegram account | |
| Chrome or another Chromium browser | |

## 2. Install

```bash
git clone <this repository> lazyhunt && cd lazyhunt
uv sync
```

## 3. Run the setup wizard

```bash
uv run cli.py init
```

The wizard asks everything first, shows a summary, and writes only after you
confirm. Press Enter to skip any step; you can run it again at any time and
it never overwrites a value without asking.

1. **Prerequisites.** It checks Python, `uv`, and `claude` (`claude --version`
   and `claude auth status`; only the "logged in" yes/no is read). If
   `claude` is missing or logged out it tells you how to fix it and goes on.
2. **Bot language.** `auto` (default: your system language, Spanish if it
   starts with `es`, English otherwise), `en` or `es` to force one: the
   language of the Telegram bot, the command line and the dashboard, saved as
   `ui_language`. CVs and
   letters keep following each offer's language. To change it later, edit
   `ui_language` in `config.local.toml` (or run the wizard again).
3. **AI provider.** `claude_code` by default. For `anthropic` or
   `openai_compatible`, add the API key to `.env` yourself.
4. **Telegram.** Create a bot: open [@BotFather](https://t.me/BotFather),
   send `/newbot` and follow the steps; paste the token it gives you (the
   input is hidden). Get your numeric user id from
   [@userinfobot](https://t.me/userinfobot); the bot answers only that id.
5. **Bridge secret.** A long random secret is generated for the extension.
6. **Profile directory.** Default `profiles/me` (gitignored). Choose a
   skeleton with placeholders to fill in, or a copy of the fictional demo
   profile to try things first. An existing directory is never touched.
7. **Gmail.** Only a pointer to [gmail.md](gmail.md); it stays off.

Where things go:

| File | Contents |
| --- | --- |
| `.env` (mode 600) | `CVA_TELEGRAM_TOKEN`, `CVA_BRIDGE_SECRET` |
| `config.local.toml` | `ui_language`, `llm_provider`, `owner_telegram_id`, `profile_dir` |
| `profiles/me/` | your profile |

Both files are gitignored. Secrets are read from the environment only;
values put in TOML files are ignored. Manual setup is still possible: copy
`.env.example` and `config.example.toml` and edit them.

## 4. Fill in your profile

Open `profiles/me/README.md` and work through its checklist. The essentials
are `claims.json` (your verified facts: only these can appear in a CV or a
letter), `profile.toml`, `preferences.toml` and your base CVs. The fictional
`profiles/demo` is the reference for every file's format; the
[profiles README](../profiles/README.md) shows the whole layout.
To search for kinds of job other than the built-in AI and backend ones, add
your own role families: [role-families.md](role-families.md).

To try the bot before writing anything, choose the demo profile in the
wizard (or set `profile_dir = "profiles/demo"`).

## 5. Load the extension

1. Open `chrome://extensions`, turn on **Developer mode**, click **Load
   unpacked** and select the `extension/` directory.
2. In the extension's **Options**, set the bridge URL to
   `http://127.0.0.1:8765/bridge` and paste the bridge secret. It is
   `CVA_BRIDGE_SECRET` in `.env`; the wizard can also show it once at the end.
3. In **Options › Portals**, enable the portals whose terms you accept. Each
   one shows its terms warning and asks the browser for permission to that
   single domain. Then add your searches.

## 6. Start the bot

```bash
uv run bot.py
```

Send `/help` to your bot in Telegram. Paste an offer link with
`/analyze <url>` to get your first score card, or wait for the extension's
next round (every 60 minutes by default). `/pause` stops every automatic task
at once (`/pausa` in Spanish; both work).

To run it as a service, see [deploy/README.md](../deploy/README.md).

## Revoking access

- Telegram: revoke the token with BotFather (`/revoke`).
- Gmail: delete the token file and remove the app at
  [myaccount.google.com/permissions](https://myaccount.google.com/permissions).
- Portals: disable them in the extension's Options, which also drops the
  browser permission.
- Claude Code: lazyhunt stores nothing; `claude auth logout` logs the CLI
  out.

## Next

- [architecture.md](architecture.md): how the pieces fit.
- [providers.md](providers.md), [portals.md](portals.md), [gmail.md](gmail.md).
- [adr/](adr/): why it is built this way.
