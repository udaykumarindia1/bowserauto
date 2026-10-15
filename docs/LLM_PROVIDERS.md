# LLM providers and agent configuration

BowserAuto's browser agent and script-to-scene analyzer use one provider-neutral async client
contract. No vendor SDK is required in the browser worker. The agent requests one structured action
at a time, checks the basic `{tool, args, reasoning}` response shape, and dispatches a known tool
through the registry (handlers do selective argument checks; the registry does not enforce a full
JSON Schema). It falls back to the deterministic planner when the selected provider is not
configured or unavailable. This is not a universal URL-destination policy or an approval gate for
consequential browser actions; see `docs/PROJECT_AUDIT.md`.

## Supported provider families

| Settings provider | Adapter | Notes |
| --- | --- | --- |
| OpenAI | OpenAI Chat Completions | Uses the configured model ID and API key. |
| OpenAI-compatible API | OpenAI Chat Completions | Custom base URL for vLLM, LM Studio, LocalAI, and hosted endpoints exposing standard OpenAI chat completions. API key is optional for local endpoints. |
| OpenRouter, Groq, DeepSeek, Together AI, Fireworks AI, Mistral AI, xAI | OpenAI Chat Completions | Presets supply common base URLs; model IDs remain editable. |
| Google Gemini | Gemini `generateContent` | Native API adapter with JSON mode, system instructions, usage reporting, and image input. |
| Ollama | Ollama `/api/chat` | Native local/cloud API adapter with JSON mode, image input, and token counts. No API key is needed for a local server. |
| Anthropic Claude | Anthropic Messages API | Native Messages adapter with image input and JSON-constrained prompting. |

Provider model names and API availability change over time. Use the exact model/deployment ID
currently enabled for your account or installed in Ollama; the model field is intentionally editable.
The provider presets are starting points, not a promise that every model supports vision or every
optional parameter.

## Configure in the UI

1. Sign in as an organization owner/admin and open **Settings → Organisation & AI**.
2. Choose **Use server default**, **Heuristic agent**, or a provider preset.
3. Enter the exact model ID. For an OpenAI-compatible endpoint, set its base URL (including the
   API prefix such as `/v1`). For Ollama, use the server origin, for example
   `http://localhost:11434`.
4. Enter the provider key if required and select **Save AI settings**. A saved key is encrypted at
   rest with `ENCRYPTION_KEY`; it is never returned to the browser. Leave the field blank to keep
   the saved key, or use the delete-key button to remove it.
5. Select **Test connection** after saving. This makes a small, bounded request and reports the
   provider, model, and latency without storing or displaying the generated text.

A provider test is available as an authenticated admin-only `POST /api/settings/llm/test` request.
The settings response includes provider metadata and readiness diagnostics, but never the raw key.

## Environment configuration

For a single server-level default, set `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, and optionally
`LLM_BASE_URL` / `LLM_TIMEOUT_SECONDS` in the backend environment. Organizations using **Use server
default** inherit that configuration. An explicitly selected organization provider uses only its
own encrypted key and provider-specific defaults; the server's environment key is never sent to a
tenant-selected endpoint.

For Docker Compose with Ollama running on the same container network, configure all backend and
worker containers consistently, for example:

```dotenv
LLM_PROVIDER=ollama
LLM_MODEL=qwen3:latest
LLM_BASE_URL=http://ollama:11434
LLM_ALLOWED_HOSTS=ollama
```

The Ollama service must be reachable from both the API (for connection tests) and the task workers
(for agent execution). For Docker Desktop, the reachable host may instead be
`host.docker.internal`; on Linux, configure the host-gateway/network route explicitly.

## Production security and data handling

- The worker sends page text to the selected provider and includes browser screenshots by default
  for vision-capable models. Set `AGENT_USE_VISION=false` to send page text only. Use only providers
  approved for that data and account, or use a local provider for sensitive pages.
- API keys are encrypted at rest, omitted from API responses, and never written into audit data.
  Provider errors are sanitized; redirects are disabled so an authorization header cannot be
  forwarded to another host.
- In production, plain-HTTP or private-network provider endpoints must be allowlisted by exact host
  in `LLM_ALLOWED_HOSTS`. Link-local endpoints (including cloud metadata addresses) are rejected.
  Apply outbound network/firewall policy as an additional boundary; a URL allowlist is not a
  substitute for egress controls.
- Set `LLM_TIMEOUT_SECONDS` to a bounded value suitable for the provider. Requests do not retry on
  timeouts or server errors, avoiding unexpected duplicate inference charges. An explicit 400 for
  an unsupported optional JSON, token-limit, or temperature field may trigger a bounded validation
  retry (maximum four requests total); ordinary model/auth errors are not retried.
- Automated tests use `httpx.MockTransport`; they never call real model APIs or spend credits.

## Troubleshooting

- **Authentication failed**: replace the key and verify billing/account access.
- **Endpoint or model not found**: check the base URL prefix and the exact provider model ID.
- **Rate limit or quota exceeded**: review provider limits/credits and retry later.
- **Ollama cannot be reached**: check that the API and browser worker can route to the host; in
  production, add its exact hostname to `LLM_ALLOWED_HOSTS`. Confirm the model is installed with
  `ollama list` / `ollama pull <model>`.
- **Heuristic mode is active**: verify provider, model, required key, and base URL in Settings; then
  use **Test connection**. Task execution remains available with deterministic planning if the
  hosted provider is unavailable.
