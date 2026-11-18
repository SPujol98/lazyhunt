# 0003. SQLite local storage

## Status

Accepted

## Context

The bot keeps offers, requirement extractions, scores, judgments, statuses,
history, beacons and calibration examples. It runs as one process on one
machine for one user. A database server (PostgreSQL, MySQL) or a hosted store
would add installation steps, credentials and a network surface for a data
set of a few thousand rows.

## Decision

All state lives in one SQLite file, `data/app.db`, accessed through the
repository in `cva/tracker/` (schema in `cva/tracker/schema.sql`). `data/` is
gitignored. Exports (`/panel` HTML dashboard, Excel) are generated from it on
demand.

## Consequences

- Zero setup: Python ships SQLite; backups are a file copy.
- Data never leaves the machine through storage.
- One writer process at a time. That matches the single-process bot; the CLI
  and the bot should not run heavy writes concurrently.
- Moving to another machine means copying `data/app.db` (and the profile
  directory) by hand.
