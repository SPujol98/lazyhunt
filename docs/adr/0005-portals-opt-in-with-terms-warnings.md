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
- The bot can optionally narrow the set further with a hand-set
  `enabled_portals` (an advanced setting, unset by default); when it is set,
  the bot rejects offers from portals not in that list.
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

## Update 2026-11-25: opt-in lives only in the extension

The `/setup` questionnaire used to have a portals step that wrote
`enabled_portals`, so a portal had to be enabled three times: in `/setup`, in
the extension's Options › Portals and in each search. The step is gone: the
opt-in (terms warning, confirmation, single-host permission) happens only in
the extension, and each search then names the portals it runs on.
`enabled_portals` remains a manual advanced setting that the bot still
enforces when set; a list an older `/setup` wrote is removed once, on the
bot's next start, and a list set by hand afterwards is kept.
