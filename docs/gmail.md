# Gmail sync (read-only)

Every `gmail_poll_minutes` (default 60) the bot reads new Gmail messages,
recognises replies to applications you have **sent** (interview invitations,
technical tests, offers, rejections), matches them to the offer and asks you on
Telegram whether to record the new status. **Nothing changes without your
approval**, and nothing is ever written to or sent from your mailbox.

It is **off by default** (`gmail_enabled = false`): no network call and no
credential is touched until you configure it.

## What the bot does with each message

1. Lists mail received since the last checkpoint (first run: the last 7 days),
   skipping Promotions, Social, Sent and Spam.
2. Matches the message to a sent, still-open offer: sender domain looks like the
   company, or the company name appears in the sender name, subject or body
   (job boards and ATS senders such as Greenhouse, Lever, LinkedIn, InfoJobs or
   Teamtailor only count when the company name is in the message). Messages
   that match no offer are ignored silently.
3. Classifies it with keyword rules (ES/EN/CA). Only matched messages the rules
   cannot decide are sent to your configured LLM provider, as untrusted data,
   truncated to 2,000 characters; the answer is one of screening,
   entrevista_tecnica, entrevista_final, oferta, rechazada or none.
4. Sends one Telegram proposal per message, never twice, and never when the
   offer already has that status or a later one. While a proposal for an offer
   and status is still unanswered, further emails pointing to the same status
   (a reminder, a calendar invite) are stored as evidence and not sent again.
   A send that times out is assumed delivered and never repeated:

   > 📧 Acme · «Entrevista»: parece screening. ¿Marcar la oferta #12 como screening?
   > [Sí] [Otro estado] [Ignorar]

   - **Sí** records the status exactly like `/outcome` (so the calibration
     question and note prompt follow).
   - **Otro estado** opens the usual outcome menu for that offer.
   - **Ignorar** drops the proposal.
   - Pressing a button twice, or on a proposal you already outran, only gets a
     polite answer.
   - Answering one proposal (any button) also resolves the offer's other
     unanswered proposals: their cards turn into "✅ Ya resuelta (#12 →
     screening)" without buttons, so there is only ever one card to click.

If Google rejects the stored authorization (revoked or expired), the bot sends
**one** warning with the exact command to run, and stays quiet until a run
succeeds again. Other API or network errors are logged and retried on the next
poll. The `/help` text has no line for these buttons (it is at Telegram's
length limit); this document is their reference.

## Setup

### 1. Google Cloud (once)

In a Google Cloud project: enable the **Gmail API**; configure the OAuth consent
screen (user type External, publishing status "In production" is fine for
personal use, the app stays unverified); add only the scope
`https://www.googleapis.com/auth/gmail.readonly`; create an **OAuth client ID of
type Desktop app** and download its JSON. Keep that file outside any repository.

### 2. Authorize on your own PC (has a browser)

```
uv run cli.py gmail-auth --credentials /path/to/gmail_credentials.json --token /path/to/gmail_token.json
```

A browser tab opens; approve read-only access. The token is written with
permissions `0600`. Without options the command uses `gmail_credentials_path`
and `gmail_token_path` from your settings (defaults
`data/gmail_credentials.json` and `data/gmail_token.json`).

Google shows an "unverified app" warning for personal-use apps: choose
Advanced, then continue.

### 3. Copy the two files to the bot PC

Copy the credentials JSON and the token to the machine that runs the bot (any
private channel; never commit them; `data/` is gitignored) and restrict them:

```
chmod 600 gmail_credentials.json gmail_token.json
```

The bot refreshes the access token by itself and writes the refreshed token back
to the same file (still `0600`).

### 4. Configure and restart

In `config.local.toml` on the bot PC (see `config.local.toml.example`):

```toml
gmail_enabled = true
gmail_credentials_path = "~/.config/cva/gmail_credentials.json"
gmail_token_path = "~/.config/cva/gmail_token.json"
gmail_poll_minutes = 60   # minimum 30; lower values are rejected at startup
```

Paths accept `~`. Restart the bot; the first poll runs right away and then every
`gmail_poll_minutes`.

## Privacy


- Read-only scope only (`gmail.readonly`); the bot cannot send, delete or label.
- Stored per proposal in the local SQLite table `gmail_messages`: message id,
  sender, subject, received date, proposed status, linked offer and your
  decision. **Message bodies are never stored.**
- A message body is read in memory to match and classify it. It leaves your
  machine only for matched messages the keyword rules cannot classify, sent to
  the LLM provider you already use for the bot (`llm_provider`), truncated to
  2,000 characters.
- Logs never contain bodies, tokens or credentials.
- The token and credentials files are secrets: keep them `0600` and out of git.

## Failures and retries

- A message that cannot be fetched holds the checkpoint and is retried on the
  next poll; after 3 failed runs it is skipped so it cannot block newer mail.
- An LLM outage is not counted against any message: the run stops, the
  message is retried on the next poll, and after 3 such runs in a row you get
  one Telegram notice (not repeated until it works again).
- A very large backlog is walked back over several polls; the checkpoint only
  moves once the whole window has been covered, so nothing is skipped silently.
  You get one Telegram notice that the window is long.
- Google throttling (HTTP 403 rate/quota errors) is retried on the next poll;
  only a real authorization failure asks you to re-run `gmail-auth`.
- If recording an approved proposal fails, the proposal reopens and the buttons
  stay on the card so you can press "Sí" again.

## Revoking access

Either remove the app at https://myaccount.google.com/permissions (the bot then
sends its single re-authorization warning), or set `gmail_enabled = false` and
delete the token file. Re-enable later by running `gmail-auth` again.
