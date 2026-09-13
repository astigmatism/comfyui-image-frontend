# Creative Direction router audit — September 12, 2026

The original integration did not meet the Nighttime routing expectation. Two live incompatibilities were reproduced and corrected in this checkout: requests omitted the model and therefore used Daytime, and the native generation endpoint rejected enabled thinking. The corrected adapter explicitly selects `nighttime` and uses the router's native chat endpoint for both Creative Direction modes.

The changes have been tested locally against the actual home router. The deployed application has not been rebuilt or restarted as part of this audit.

## Router observations

The router at `http://192.168.1.21:11434` answered both `/api/tags` and `/v1/models`. The supplied `Authorization: Bearer local-only` header was accepted. An unauthenticated native request also succeeded; this audit does not establish that the router enforces authentication.

| Observation | Live result |
| --- | --- |
| Advertised `nighttime` alias | Available; resolved to `qwen3.8-27b-abliterated-q6_k`, advertised context 32,768 tokens |
| Advertised `daytime` and `local-active` aliases | Available; resolved to `qwen3.8-27b-q8_0` |
| `/api/generate`, model omitted, thinking off | HTTP 200, Daytime backend, 1.68 seconds |
| `/api/generate`, `nighttime`, thinking off | HTTP 200, Nighttime backend, 1.89 seconds |
| `/api/generate`, `nighttime`, thinking on | HTTP 400, `REASONING_ROUTE_UNSUPPORTED` |
| `/api/chat`, `nighttime`, thinking on | HTTP 200, Nighttime backend, structured final prompt and separate thinking, 6.05 seconds |
| `/v1/chat/completions`, `nighttime`, supplied bearer header | HTTP 200, Nighttime backend and schema-constrained prompt |

These model identities are observations at audit time. The application selects the alias and does not hardcode either physical model name. The router continues to control which backend implements Nighttime.

The rejected native generation response said: “Enabled reasoning is supported on chat routes, not /api/generate.” The local router implementation independently confirms this restriction in `src/backend-adapters.js`, function `normalizeLlamaReasoningRequest`. Its structured-output adapter also maps native JSON schemas to the backend's JSON-schema response format.

## Application changes

- `CIF_OLLAMA_MODEL` now defaults to `nighttime` and is sent on every composition attempt, including retries. An explicit empty value preserves intentional router-default selection.
- `CIF_OLLAMA_API_KEY` supplies an optional server-only bearer header for discovery and composition. The secret is masked in settings representations and excluded from saved composition diagnostics.
- `CIF_OLLAMA_BASE_URL` accepts the router root or the supplied `/v1` base, normalizing the latter to the native API root.
- Compositions use `POST /api/chat` with a user message, `stream: false`, the existing JSON schema, sampling controls, output budgets, and `think: "xhigh"` when thinking is enabled or `think: false` when disabled.
- Chat `message.content` and `message.thinking` are normalized into the existing structured-output parser. Complete final content takes priority; unstructured reasoning is never used as a prompt.
- Availability requires the configured alias, including rejecting an alias whose router metadata explicitly marks its backend offline. Another available model does not silently substitute for Nighttime.
- Successful runs continue to save the effective response model, original input, direction, final prompt, thinking setting, and composition provenance. The composition output remains authoritative when preparing a generation.

The native chat request and response fields match [Ollama's chat API documentation](https://docs.ollama.com/api/chat). Using this interface preserves the existing thinking controls and bounded retry policies; the application does not need to contact llama.cpp directly or adopt the OpenAI API to use this router.

## Both Creative Direction modes

| Mode | Sent to the LLM | Verified behavior |
| --- | --- | --- |
| New Prompt from Creative Direction (`create`) | Instruction plus Creative Direction only | A Mars greenhouse/astronaut direction produced a fresh image prompt without carrying over the unrelated ceramic-vase prompt. Four consecutive requests for a fox beneath moonlit pines produced distinct prompts. |
| Modify the existing prompt (`refine`) | Instruction plus current prompt and Creative Direction | Changing only a red umbrella to blue retained the quiet canal and, in the detailed live case, the original soft overcast light. |

The browser sends the current prompt to the application in both modes. Create uses it locally for duplicate rejection and stores it as provenance, but excludes it from the LLM message. Refine includes both inputs in the LLM message. This distinction matches the requested behavior.

Both modes passed live testing with thinking on and off. Additional authenticated application API tests used the real router with temporary accounts/storage and a fake ComfyUI service. They inspected outgoing messages, verified the requested alias, saved the result, prepared a generation with a deliberately stale browser prompt, and confirmed that recall contained the composed output. No images were generated on household ComfyUI hardware during this audit.

## Validation

- Focused backend tests: **71 passed** (existing adapter/Prompt Assistant integration coverage plus new router contract tests).
- Live router suite: **7 passed**, covering both modes with thinking on/off, repeated creation, and both modes through the authenticated application API and persistence path.
- Frontend unit/render/build tests: **115 passed**.
- Strict mypy: passed for all 46 application source files.
- Ruff lint and formatting: passed for changed Python files.
- Generated requirement traceability and `git diff --check`: passed.
- Browser journeys: **12 passed**, covering focused-editor draft isolation, Create submission, visible failures, Auto-generate composition ordering, retries, cancellation, input changes, and prepared compositions.

The live suite initially demonstrated the thinking-on failures; those passed after switching to chat. An initial filtered browser run selected a Favorites test without its prerequisite account-creation test. The corrected scope runs the self-contained Creative Direction journeys. The full repository test suite and a production Docker deployment were outside this focused audit.

To repeat the live verification with the project's Python environment:

```sh
CIF_RUN_LIVE_OLLAMA_TESTS=1 \
CIF_OLLAMA_BASE_URL=http://192.168.1.21:11434/v1 \
CIF_OLLAMA_MODEL=nighttime \
CIF_OLLAMA_API_KEY=local-only \
PYTHONPATH=backend python -m pytest -q backend/tests/live/test_ollama_integration.py
```

Recommended deployment configuration:

```dotenv
CIF_OLLAMA_BASE_URL=http://192.168.1.21:11434/v1
CIF_OLLAMA_MODEL=nighttime
CIF_OLLAMA_API_KEY=local-only
```

The root URL without `/v1` is equivalent. Rebuild/restart the application with this checkout and configuration to apply these fixes to the running service. No router configuration or model changes are required.

## Thinking effort follow-up

The live `/api/tags` metadata for `nighttime` advertises `xhigh` as its highest reasoning level and `max` as an alias for `xhigh`. It explicitly maps boolean `true` to the `default` effort. The application's earlier boolean request therefore enabled thinking without requesting the highest level.

Enabled compositions now explicitly send `think: "xhigh"` on the native chat route. The router's `normalizeLlamaReasoningRequest` resolves that named level and forwards `reasoning_effort: "xhigh"` with `enable_thinking: true` to its backend. Disabling the checkbox still sends `think: false`. The application continues to use the `nighttime` alias and the same mapping for Create, Refine, Auto-generate, and retries. Existing output-token allowances remain separate from the requested reasoning effort.

Two direct live requests to Nighttime confirmed both accepted spellings: `xhigh` returned HTTP 200 in 8.39 seconds and `max` returned HTTP 200 in 9.78 seconds. Both completed their structured image prompt and returned separate, non-empty thinking output. Raw reasoning was not recorded in this report.

The **Thinking mode** checkbox now sits inside **Prompt pre-processor** in both the sidebar and focused editor. Both sections start collapsed, thinking defaults on, and collapsing the section preserves the selected setting. Focused-editor Cancel discards changes; Apply saves them back to the sidebar.

Follow-up validation passed: **77 focused backend tests**, **9 live router tests**, **115 frontend tests**, and **5 browser journeys**, plus frontend build/lint/format checks, Python lint/format/type checks, and `git diff --check`. Live application API coverage checks outgoing `xhigh`/`false` requests for both modes and persisted thinking settings. Browser coverage checks default-on behavior, hidden controls, changed settings while collapsed, draft isolation, Auto-generate, and visible errors. The local preview was restarted with the updated adapter and inspected in the in-app browser.
