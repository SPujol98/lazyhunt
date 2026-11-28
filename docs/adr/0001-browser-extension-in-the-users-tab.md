# 0001. Browser extension in the user's own tab instead of server-side scraping

## Status

Accepted

## Context

LazyHunt needs new job offers from portals such as InfoJobs, Indeed or
LinkedIn. The obvious design is a server-side scraper: a headless browser or
an HTTP client that fetches search pages on a schedule. In practice:

- Most portals block datacenter and headless traffic (Cloudflare, login
  walls, anti-bot challenges), so a scraper needs proxies, CAPTCHA solving or
  stored session cookies, all of which escalate the conflict with the
  portal's terms.
- Storing a user's portal session cookies on a server is a credential-handling
  problem we do not want.
- Some portals' robots.txt disallow AI crawlers.
- Many listings are only complete for a logged-in user.

## Decision

Offers are read by a Chromium extension (`extension/`) running in the user's
own browser, in the user's own logged-in tab, at low volume: one round every
60 minutes by default, a warning below 30, and per-beacon and global daily
caps. The extension sends only offers it has not delivered before to the
local bot over the loopback bridge (`cva/bridge.py`). The bot itself makes at
most one plain request for an `/analyze <url>` the user asks for, and none
for portals marked in-tab only (`server_fetch=False`).

## Consequences

- No proxies, no stored cookies, no headless browser: the browser handles its
  own session and the bot never sees portal credentials.
- Scans only run while the browser is open on the user's machine.
- Each portal needs a small DOM adapter (`extension/portals/`) that can break
  when the site changes; adapters are kept small and tested against sanitized
  fixtures.
- Reading pages in the user's tab is still automated access. Some portals'
  terms prohibit it, which is why every portal is opt-in
  ([0005](0005-portals-opt-in-with-terms-warnings.md)).
