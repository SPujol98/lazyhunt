# AI providers

cva needs a language model to extract offer data and judge matches. Pick one
provider with `llm_provider` in `config.toml` / `config.local.toml`. Secrets
(API keys) are read from the environment only, never from the TOML files.

| `llm_provider` | Uses | Tested |
| --- | --- | --- |
| `claude_code` (default) | your installed Claude Code CLI | yes |
| `anthropic` | the Anthropic Messages API | yes |
| `openai_compatible` | any OpenAI-style `/chat/completions` endpoint | yes with Gemini (free-tier key); others at your own risk, see below |
| `codex_cli` | your installed Codex CLI (`codex exec`) | with a fake CLI only; a real run is pending |

## The AI judge scores the offers (`match_mode`)

The provider is what scores each offer against your profile: with
`match_mode = "hybrid"` the AI judge gives the 0–100 score and verdict that
decide which offers reach Telegram (the keyword breakdown is kept on the card
for transparency). `cli.py init` writes `match_mode = "hybrid"` on a new
install; it never changes a `match_mode` you already set.

`match_mode = "keyword"` (the code default, kept for existing installs) is an
offline fallback that uses no AI: it only counts keywords, so its scores
rarely reach `threshold` and few or no offers are notified. Use it only when
no provider is available.

```toml
match_mode = "hybrid"
```

## 1. Claude Code (default)

cva runs your own, unmodified `claude` CLI in headless mode (`claude -p`),
logged in with your own subscription. It never reads, copies or forwards the
CLI's credentials; the CLI handles its own login.

```toml
llm_provider = "claude_code"
# claude_code_path = "claude"   # full path if it is not on PATH
# claude_code_model = "sonnet"
```

The child process does not inherit `ANTHROPIC_API_KEY` or
`ANTHROPIC_AUTH_TOKEN` from your environment: if it did, the CLI would
silently switch from your subscription to API billing. To let it use them on
purpose, set `claude_code_pass_api_key = true`. `CVA_*` variables (this app's
own secrets) are never passed either way.

Claude Code and Codex CLI are the providers that can research company facts
and salaries on the web; the HTTP providers answer "not available" instead of
guessing.

## 2. Anthropic API

```toml
llm_provider = "anthropic"
# anthropic_model = "claude-sonnet-4-5"   # check the current model ids
# extraction_model = "..."                # optional per-step overrides
# judge_model = "..."
```

```sh
export ANTHROPIC_API_KEY=sk-ant-...      # environment only
```

cva calls `POST https://api.anthropic.com/v1/messages` directly (no SDK) with
structured JSON output. Model ids change over time: pick one from
<https://docs.anthropic.com/en/docs/about-claude/models> and set
`anthropic_model` (a cheaper model for `extraction_model`, a stronger one for
`judge_model`, for example). The key is billed per use against your API
account.

## 3. OpenAI-compatible (other providers)

```toml
llm_provider = "openai_compatible"
llm_base_url = "https://api.openai.com/v1"
llm_model = "gpt-4o-mini"
# llm_disable_thinking = false   # GLM-style endpoints
# llm_429_retry_wait_seconds = 25
# llm_transient_retries = 2      # extra attempts on HTTP 5xx/timeouts (~2 s, ~5 s); 0 = off
```

```sh
export CVA_LLM_API_KEY=...               # environment only
```

Examples (`llm_base_url` / `llm_model`; confirm model names with the provider):

| Provider | `llm_base_url` | Example `llm_model` |
| --- | --- | --- |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| OpenRouter | `https://openrouter.ai/api/v1` | `openai/gpt-4o-mini` |
| Groq | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |
| Mistral | `https://api.mistral.ai/v1` | `mistral-small-latest` |
| Gemini (OpenAI-compatible endpoint) | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-3.5-flash-lite` |
| Ollama (local) | `http://localhost:11434/v1` | `llama3.1` |
| LM Studio (local) | `http://localhost:1234/v1` | the model id LM Studio shows |

Gemini (tested with a free-tier AI Studio key on 2026-11-21): `gemini-3.5-flash-lite` (fast, cheap) and `gemini-3-flash-preview` (stronger) both completed extraction and judging. Model ids change and old ones are retired (HTTP 404 "no longer available to new users"); the `gemini-flash-latest` alias did not answer for a new key at that date (requests timed out), so pick an explicit id from AI Studio's model list. The free tier can answer HTTP 503 "high demand" at busy times; the bot retries twice with a short backoff and then shows the provider's message.

Local servers ignore the key but cva still requires `CVA_LLM_API_KEY` to be set
(any non-empty value).

**Tested vs. at your own risk.** Claude Code, the Anthropic API and Gemini's
OpenAI-compatible endpoint are tested against the real service; Codex CLI is
tested with a simulated CLI only. For any other provider the endpoint must support JSON-schema
structured output (`response_format: json_schema`); models or servers that do
not will fail with "completion content contains no JSON object" or schema
errors. Use them at your own risk and check the first results by hand.

## 4. Codex CLI

cva runs your own, unmodified `codex` CLI non-interactively (`codex exec`),
logged in with your own ChatGPT account (`codex login`). It never reads,
copies or forwards the CLI's credentials; the CLI handles its own login.

```toml
llm_provider = "codex_cli"
# codex_cli_path = "codex"          # full path if it is not on PATH
# codex_cli_model = "gpt-5"         # omit to use the CLI's default model
# codex_cli_timeout_seconds = 180
```

Each call runs, roughly:

```sh
codex -a never exec --sandbox read-only --ephemeral --skip-git-repo-check \
  --ignore-user-config --color never -C <empty temp dir> \
  --output-schema <schema.json> -o <last-message.txt> \
  -c web_search="disabled" [-m <model>] -
```

The prompt goes through stdin, the answer shape is enforced with
`--output-schema`, and cva reads only the final message file. The CLI works in
an empty temporary folder with a read-only sandbox, never asks for approval,
keeps no session and does not load your `~/.codex/config.toml` (so your MCP
servers and hooks are not used; your login still is).

The child process does not inherit `OPENAI_API_KEY`, `CODEX_API_KEY` or
`CODEX_ACCESS_TOKEN` from your environment: if it did, the CLI could switch
from your ChatGPT plan to API billing. To let it use them on purpose, set
`codex_cli_pass_api_key = true`. `CVA_*` variables are never passed either way.

Web research (salary and company facts, on demand only) adds Codex's
`--search` flag, which turns on its live web search tool for that call; all
other calls keep web search disabled.

If the CLI is not logged in, the error tells you to run `codex login`.

**Tested status:** tested with a fake CLI only; a real run is pending.

## Which one is active?

The bot logs one line at startup, and `cli.py rejudge` / `judge-eval` print it
first, for example `LLM: proveedor=claude_code · extracción=default ·
juez=default`. It never contains a secret.
