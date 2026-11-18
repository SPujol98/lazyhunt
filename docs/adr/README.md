# Architecture decision records

Each record explains one design choice: the context, the decision and what
follows from it. Format: Status, Context, Decision, Consequences.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-browser-extension-in-the-users-tab.md) | Read portals with a browser extension in the user's own tab, not server-side scraping | Accepted |
| [0002](0002-users-own-claude-code-cli-as-default-provider.md) | The user's own Claude Code CLI is the default AI provider | Accepted |
| [0003](0003-sqlite-local-storage.md) | Store everything in a local SQLite file | Accepted |
| [0004](0004-bring-your-own-google-oauth-client.md) | Gmail through a bring-your-own Google OAuth client, read-only | Accepted |
| [0005](0005-portals-opt-in-with-terms-warnings.md) | Portals are opt-in, with terms warnings and per-domain permission | Accepted |

New records take the next number and keep the same headings. A decision that
changes is not edited away: add a new record and mark the old one
"Superseded by NNNN".
