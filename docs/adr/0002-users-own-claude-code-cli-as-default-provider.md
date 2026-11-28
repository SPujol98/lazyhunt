# 0002. The user's own Claude Code CLI as the default AI provider

## Status

Accepted

## Context

Judging offers, extracting requirements and drafting letters need a capable
language model. Options:

- A shared API key owned by the project: impossible for an open-source,
  self-hosted tool (cost, abuse, terms).
- Asking every user for an API key: a real billing account and a secret to
  manage, for a personal tool.
- Reusing a subscription the user already has, by driving the official CLI.

Driving the CLI must not turn into credential handling: reading, copying or
forwarding the CLI's tokens would be both a security risk and a terms
problem.

## Decision

The default provider (`llm_provider = "claude_code"`) runs the official,
unmodified `claude` CLI that the user installed and logged in to, as a child
process in headless mode (`claude -p`). LazyHunt never logs the user in,
never reads or stores the CLI's credentials, and the child process does not
inherit `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` unless the user sets
`claude_code_pass_api_key = true`, so it never silently switches to API
billing. The setup wizard checks only `claude --version` and the `loggedIn`
flag of `claude auth status --json`.

The provider sits behind one protocol (`cva/llm.py`), so the Anthropic API
and OpenAI-compatible endpoints are available as alternatives
([providers.md](../providers.md)).

## Consequences

- No shared keys and no secrets for the default path; usage counts against
  the user's own plan and its limits.
- The project depends on the CLI's headless interface and output format; a CLI
  change can need an adapter update.
- The README states that the project is independent and not endorsed by
  Anthropic, and asks users to check Anthropic's terms for their plan.
- Automatic analysis spends model calls on every new offer; pacing settings
  (`analyze_delay_seconds`, `auto_analyze`) and the kill switch keep usage
  under control.
