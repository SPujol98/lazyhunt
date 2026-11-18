# AI providers

cva needs a language model to extract offer data and judge matches. Pick one
provider with `llm_provider` in `config.toml` / `config.local.toml`. Secrets
(API keys) are read from the environment only, never from the TOML files.

| `llm_provider` | Uses | Tested |
| --- | --- | --- |
| `claude_code` (default) | your installed Claude Code CLI | yes |
| `anthropic` | the Anthropic Messages API | yes |
| `openai_compatible` | any OpenAI-style `/chat/completions` endpoint | OpenAI-style endpoints in general only; see the note below |

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

This is the only provider that can research company facts on the web;
the other two answer "not available" instead of guessing.

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
| Gemini (OpenAI-compatible endpoint) | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.0-flash` |
| Ollama (local) | `http://localhost:11434/v1` | `llama3.1` |
| LM Studio (local) | `http://localhost:1234/v1` | the model id LM Studio shows |

Local servers ignore the key but cva still requires `CVA_LLM_API_KEY` to be set
(any non-empty value).

**Tested vs. at your own risk.** Only Claude Code and the Anthropic API are
tested. For any other provider the endpoint must support JSON-schema
structured output (`response_format: json_schema`); models or servers that do
not will fail with "completion content contains no JSON object" or schema
errors. Use them at your own risk and check the first results by hand.

## Which one is active?

The bot logs one line at startup, and `cli.py rejudge` / `judge-eval` print it
first, for example `LLM: proveedor=claude_code · extracción=default ·
juez=default`. It never contains a secret.
