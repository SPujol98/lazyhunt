# 0004. Bring-your-own Google OAuth client for read-only Gmail

## Status

Accepted

## Context

Replies to applications (interview invitations, rejections) arrive by email.
Reading them lets the bot propose status changes. A shared OAuth client
published by the project would need Google's verification for a restricted
scope, including a security assessment, and would make the project a party to
every user's mailbox access.

## Decision

Gmail is optional and off by default (`gmail_enabled = false`). Users who want
it create their own OAuth client (type Desktop app) in their own Google Cloud
project, with the single scope `gmail.readonly`, and authorize it with
`uv run cli.py gmail-auth` ([gmail.md](../gmail.md)). The token is stored
locally with mode 600. Message bodies are never stored; the bot proposes a
status change in Telegram and records it only after the user confirms.

## Consequences

- No shared client, no verification process, nothing sent to the author.
- Setup is longer: users go through Google Cloud's console and see an
  "unverified app" warning for their own personal client.
- Read-only scope: the bot cannot send, delete or label mail.
- Revoking access is in the user's hands (delete the token, remove the app in
  the Google account permissions page).
