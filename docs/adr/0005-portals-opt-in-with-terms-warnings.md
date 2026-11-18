# 0005. Portals opt-in, with terms warnings and per-domain permission

## Status

Accepted

## Context

Job portals have different terms. Some, notably LinkedIn, prohibit
extensions that scrape or automate the site, and an account can be
restricted. Others say nothing or only limit volume. A tool that ships with
every portal enabled would decide on the user's behalf to accept those risks,
and an extension with broad host permissions would read sites the user never
chose.

## Decision

- A fresh install scans no portal. In the extension's Options each portal has
  a toggle; enabling one shows its terms warning (a stronger one for
  LinkedIn), asks for confirmation, and requests browser permission for that
  single host (`optional_host_permissions`). Disabling it removes the
  permission.
- The bot can narrow the set further with `enabled_portals`; a portal must be
  enabled on both sides to be scanned, and the bot rejects offers from
  portals that are not enabled.
- Low volume by design: hourly rounds by default, per-beacon and global daily
  caps, and a kill switch (`/pausa`) that survives restarts.
- LinkedIn ships in the public version, disabled by default, with the explicit
  terms warning. Adapters written only for the original author's own use are
  not part of the public version.

## Consequences

- The user, not the project, decides which terms to accept; the README and
  the getting-started guide repeat the warning.
- A first run shows nothing until the user enables at least one portal, which
  the setup wizard and the docs explain.
- Each new portal adapter must declare its host and terms note
  ([portals.md](../portals.md)).
