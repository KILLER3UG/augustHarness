# Audit — tools/MCP/sandbox/permissions + LLM routing/local models

Repo: C:/Dev/augustHarness @ master (9804b069). Read-only static audit of backend-py/app (areas 7–8). Every claim below was checked against the current working tree; line numbers are from this tree. Note: two earlier working hypotheses about `gateway_auth.py` (a hardcoded `DEFAULT_KEY`, and blank-key-fails-open) were re-verified and are FALSE — the file fails closed; see Strengths.

## Verdict (one paragraph)

The tool/MCP/sandbox/permission stack is in noticeably better shape than the known-open list suggests. The 2026-10-21 fix that routed /v1-proxy and MCP tool calls through `_executeTool` is complete: the proxy adapter (`proxy_tools.py:378-399`), the subagent worker path, and the `tool_call` bridge all reach the registry through the single choke point with PRE/POST hooks, per-tool timeouts, `_expect` receipts, and the shared guard. The gateway and per-install token doors fail closed (403/503/401 matrix, `hmac.compare_digest`), guard-mode normalization clamps unknown spellings to the most restrictive mode, MCP tools are mutating-by-default, secrets at rest use DPAPI, and the hardline command tokenizer with the `.git/` push-lock and `.ssh` allowlist is thorough. The real residual risk is concentrated in plaintext credential material: a live Google OAuth client secret sits in `data/mcp-servers.json` in the working tree, service tokens are exported to `os.environ` where the outbound scrub cannot see them (it only sweeps `*_API_KEY` env names), and the per-server MCP env map is outside the scrub's collection set entirely. One code-level gap remains from the unification: the `tool_call` bridge's no-session fallback still dispatches raw, skipping hooks and (when `get_session()` returns None rather than raising) the guard. Routing is evidence-only as decided: the arena endpoints record and rank, nothing auto-reroutes.

## Findings (ordered by severity)

### [HIGH] Live Google OAuth client secret in plaintext in the working tree's MCP server config
- What I saw: `data/mcp-servers.json` (repo root `data/`, untracked; `.gitignore:29` covers `data/`) registers server `mcp_85fdd140` (Google Workspace MCP) with `env.GOOGLE_OAUTH_CLIENT_SECRET` holding a literal `GOCSPX-…` value in the clear, alongside `GOOGLE_OAUTH_CLIENT_ID` and `OAUTHLIB_INSECURE_TRANSPORT: "1"` (file read in full this session; value deliberately not reproduced). This realizes the known-open item "mcp-servers.json per-server env map can hold a token in the clear". The API responses mask env (`redactedServerRow`, `backend-py/app/services/tools/mcp_client.py:164`) but the on-disk file is plaintext.
- Why it matters: Anyone who can read the data dir (any local process, or a model with a read tool if path doors were bypassed) gets a Google OAuth client secret. It is also outside the outbound scrub's collection set (see MED finding below), so if it ever lands in a transcript it would not be scrubbed on the way to the provider.
- What good looks like: Store per-server env secrets as secret-store references resolved at spawn time (the `${NAME}` resolution door already exists — `backend-py/app/services/tools/chat_secrets.py:64-96`), keep only non-secret env in `mcp-servers.json`, and rotate the exposed secret.

### [HIGH] GITHUB_TOKEN / SLACK_BOT_TOKEN exported to os.environ; outbound scrub cannot see token-class env vars
- What I saw: `backend-py/app/services/service_connections.py:493` `os.environ['GITHUB_TOKEN'] = token.strip()` and `:516` `os.environ['SLACK_BOT_TOKEN'] = bot_token.strip()` (connect handlers; tokens also stored via the encrypted secret store). The outbound scrub's env collection only sweeps names ending in `_API_KEY` (`backend-py/app/services/hooks/secret_guard.py:114`), so `_TOKEN`-suffixed vars are invisible to `scrubOutboundBody` even though its other sources (providers.json, secret store, gateway key) are covered (`secret_guard.py:120-140`).
- Why it matters: This is the known-open item, confirmed still present. Exported env is inherited by spawned children (the `HARNESS_*` scrub at spawn covers the local-token pointers, not these), and a token that leaks into a model-visible read result will pass through the value-based scrub untouched — the exact leak class the scrub exists to stop.
- What good looks like: Resolve service tokens at use-time from the secret store instead of exporting them; or extend the scrub's env scan to a named set (`GITHUB_TOKEN`, `SLACK_BOT_TOKEN`, `*_TOKEN`) so value-matching covers what the process actually holds.

### [MED] tool_call bridge's no-session fallback dispatches raw — no hooks, and no guard when get_session() returns None
- What I saw: `backend-py/app/services/tools/tool_bridges.py:151-155` runs `_checkToolGuard` only when `get_session()` returns a session; the fail-closed branch at `:156-165` covers a raised exception, not a None return. After the subagent deny check (`:170-193`), dispatch tries `_executeTool` (`:198-205`) and on any exception falls through to raw `dispatch(name, args)` at `:212` — no PRE/POST hooks, no read-before-edit gate, no mutation log (comment at `:194-197` acknowledges the raw path).
- Why it matters: This is the only remaining path that reaches the registry without `_executeTool` — i.e., the residual hole after the 2026-10-21 fix (verified complete everywhere else: proxy path `adapters/proxy_tools.py:378-399`; subagent worker `services/workbench/subagent.py:777` deny-set wiring feeding the worker exec path; native loop `services/workbench/loop/exec.py:166-266`). A context that calls `tool_call` with no bound session gets hook-free execution of arbitrary registered tools, including mutating ones.
- What good looks like: Fail closed when no session is bound (mirror the `:156-165` pattern: `is_mutating` → block, reads → raw dispatch at most), or bind an explicit system session so hooks always run.

### [MED] MCP server create/update/delete lack the per-install token that start/stop require
- What I saw: `backend-py/app/routers/mcp.py:117` (POST create), `:158` (PATCH update), `:224` (DELETE) carry only `require_gateway_key` + trusted-origin/LAN gates, while `:232` (start) and `:272` (stop) also depend on `require_local_token` — the docstring in `backend-py/app/lib/local_api_auth.py:20-21` lists only `/api/mcp/servers/{id}/start|stop`.
- Why it matters: A non-browser local process (no `Origin` → `TrustedOriginGuard` passes by design, `backend-py/app/lib/local_api_guard.py:21-22`) can register an arbitrary stdio `command` without the token, and execution begins later via the background discovery refresh that spawns every registered server (`mcp_client.py:1485-1504` → `_startServerProcess`, `:144-161`). `validateStdioLaunch` (`mcp_client.py:127-141`) constrains the binary to an allowlist, which makes this accidental-misconfiguration armor rather than an exfiltration door — but the asymmetry against start/stop is unexplained.
- What good looks like: Put `require_local_token` on the mutating MCP-server routes too; the token is already in hand for the SPA and it closes the "register now, spawn later" gap for scripted local actors.

### [MED] Per-server MCP env secrets are outside the outbound scrub's collection set
- What I saw: `scrubOutboundBody`'s collectors cover providers.json (apiKey/baseUrl/defaultHeaders), the secret store, the gateway key, and `_API_KEY`-suffixed env (`backend-py/app/services/hooks/secret_guard.py:114-140`). Values placed in a server's `env` map in `mcp-servers.json` (e.g. the HIGH finding above) are collected nowhere, and `mcpGlobalEnv` resolution feeds `${VAR}` expansion (`chat_secrets.py:64-96`) without feeding the scrub.
- Why it matters: The scrub is value-based precisely so a secret leaks nothing "by pattern" — but a secret the collector never sees is scrubbed never. Combined with the plaintext env map, a leaked MCP token would transit to the provider unnoticed.
- What good looks like: Add `_servers` env values (and `mcpGlobalEnv`) to `collectOutboundSecrets` so anything August would inject into a child process is also something August will scrub outbound.

### [LOW] Unknown-model pricing falls back to the most expensive known model
- What I saw: `price_for_model` (`backend-py/app/services/cost_estimator.py:200`) is the single pricing door; for an unpriced model the estimator returns the max-priced known model as a "conservative upper bound" (in-code documented at the tail of the module, ~`:255-266`).
- Why it matters: Cost estimates for unpriced/local models can be materially inflated; harmless for enforcement (nothing blocks on cost) but misleading in reports.
- What good looks like: Return "unknown" (None) for unpriced models and let the UI render an estimate range instead of a confident wrong number.

### [LOW] EventSource paths accept the per-install token in the query string
- What I saw: `backend-py/app/lib/local_api_auth.py:160-163` allows `august-token` in the query for `/api/realtime/stream` and `/api/monitor/events/stream` (browser EventSource cannot set headers), with `hmac.compare_digest` (`:391-393`).
- Why it matters: Tokens in URLs leak via proxy/access logs and referrers. Scope is narrow and LAN-mode only, but it is still a credential in a URL.
- What good looks like: Short-lived single-use ticket issued via a header-authenticated POST, exchanged for the stream — or accept the risk explicitly in the docstring (it is currently justified only by browser limitation).

### [LOW] Hardline's command matcher is a best-effort denylist around a solid tokenizer
- What I saw: `backend-py/app/services/sandbox/hardline.py` — single `_tokenize` (`:455-460`), `.ssh` public-member allowlist (`:417-425`), `.git/`/`.gitconfig`/`.git-credentials` push-lock regex (`:61-68`), with the substring-matching false-positive caveat documented (`:462-473`: "a file whose name merely contains…").
- Why it matters: Denylist matching is acknowledged as best-effort (suffix false positives, novel shells); the real boundary is the sandbox tier/AppContainer. Fine as defense-in-depth; worth remembering it is not the wall.
- What good looks like: Keep as is; consider shipping the OS-tier (AppContainer) as the default rather than opt-in so a matcher miss is not the last line.

### [LOW] workspace-write tier does not confine writes to the workspace on POSIX; Windows tier is fixed
- What I saw: `backend-py/app/services/sandbox/policy.py:105-112` — Windows currently always resolves to `workspaceWrite`; `:115-119` — on other OSes `workspaceWrite` allowlists Home/Temp/Ephemeral and does NOT add the workspace root as a writable cap (comment: chroot not needed "yet").
- Why it matters: "Workspace-write" is a misnomer outside the OS-tier path: writes are bounded by the filesystem, not the workspace. Documented, but easy to over-trust.
- What good looks like: Either add the workspace root to the writable set on POSIX tiers or rename the tier to reflect "home-write minus read-only doors".

### [LOW] Dead/belt-and-suspenders code in the transport and MCP read loop (removal candidates)
- What I saw: (1) `BaseProviderClient.streamSse` carries a `wait_for=None` branch (`backend-py/app/providers/clients/base.py:698-700` region) but every call site passes a timeout (all eight `streamSse(...)` call sites in `base.py:492-593` pass `_timeout`), so the branch is dead; (2) `backend-py/app/services/tools/mcp_client.py:1206-1208` — a duplicated `break` immediately after `break` in the SSE read loop; (3) `backend-py/app/services/sandbox/runner.py` (~50 lines) is a thin pass-through over `hardline`/`policy` that adds a name only.
- Why it matters: Dead branches in the transport invite future edits to an untested path; the duplicate `break` is noise; the runner indirection hides where sandbox decisions actually live.
- What good looks like: Make the timeout parameter required in `streamSse` (or raise on None), drop the duplicate `break`, and fold `runner.py` into `hardline.py` (or make it the single public door and stop importing hardline directly).

## Strengths worth keeping

- **One execution choke point.** `_executeTool` (`services/workbench/loop/exec.py`) is the only path to the registry: PRE hooks can deny/modify (`exec.py:166-190`), MCP calls share the exact lifecycle + `_TOOL_EXEC_TIMEOUT_S` timeout as resident tools (`:194-222`, default 300s via `HARNESS_TOOL_TIMEOUT_S`, floor 30s at `:37-46`), POST hooks can rewrite results (`:228-250`), and `_expect` receipts are evaluated and appended (`:86-96`, `:262-266`).
- **MCP tools are mutating by default.** `tool_policy.is_mutating` prefix rule `mcp__` → True (`tool_policy.py:383-388`), so plan/ask modes gate every MCP tool even before discovery knows what it does.
- **The parse door held.** Every tool-argument entry point routes through `json_salvage.parse_tool_arguments` (adapters `anthropic.py:626/1179/1305`, `proxy_tools.py:54/463`, `stream_state.py:71`, bridge `tool_bridges.py:137-143`); malformed JSON yields `_raw` → honest refusal, never `{}` phantom execution (`proxy_tools.py:455-465`).
- **Fail-safe guard modes.** `normalizeGuardMode` clamps unrecognized spellings to the most restrictive mode (`loop/surface.py:56-60`); `_checkToolGuard` (`workbench.py:6756-6952`) handles read-only sandbox (`:6771-6778`), self-deletion block in every mode (`:6794-6823`), clone gate (`:6833-6841`), plan risk fail-closed (`:6847-6873`), edit/ask pending mutations with grant keys and unattended never-ask denials (`:6887-6951`).
- **Both network doors fail closed.** `/v1/*`: disabled→403, enabled+no key→503, missing/wrong→401, `hmac.compare_digest` (`gateway_auth.py:89-133`). LAN mode: management API needs the token, loopback is a transport fact exemption, unconfigured→503 fail-closed (`local_api_auth.py:340-371`), and the per-install token is popped out of the environment at import so children cannot inherit it (`:125`).
- **Sandbox depth.** Hardline single tokenizer + `.git/` push-lock + `.ssh` allowlist; `bind_path` fence with junction/symlink checks on Windows (`sandbox/paths.py:69-118`); egress SSRF policy checks destinations against the tier (`sandbox/egress.py`); AppContainer opt-in documented as the real confinement (`sandbox/backends/container.py:8-14`).
- **Secrets posture.** DPAPI-encrypted store with mode + checksum fields (`secret_store.py:22-28,124,192-228`); credential stores unlistable at 7 doors; outbound scrub is value-based with a no-negative-cache rule (`secret_guard.py:120-140`).
- **Routing is honest.** Arena endpoints only record verdicts and rank win rates (`routers/brain_config.py:601-665`); no code path rewrites a session's provider from evidence. Per-model `apiFormat` override wins over provider format (`workbench/providers.py:497-502` → `providers/resolver.py:49`), base URLs are used exactly as pasted (`providers/api_format.py:99-156`), loopback needs no key (`:135-156`), and `cost_estimator.price_for_model` is the only pricing source.
- **Progressive disclosure is real.** BM25-budgeted tool selection against the model's actual context window (`workbench.py:1696-1733`), deferred schemas reachable via `tool_search` (`workbench.py:3558`, `tool_bridges.py:108-129`), with a two-layer cache keyed on registry generation + MCP signature (`tool_defs_cache.py:95-116`).

## Coverage ledger

Files opened and read (this session, current tree):
- Tools/registry/bridges: `services/tool_registry.py` (1-300, 440-679), `services/tool_policy.py` (full), `services/tools/tool_bridges.py` (full), `services/tools/mcp_client.py` (full, 4 chunks), `services/tools/chat_secrets.py` (full), `services/workbench/tool_defs_cache.py` (full), `services/workbench/json_salvage.py` (1-130), `scripts/check-tool-registration.mjs` (existence + package.json:29 wiring).
- Loop/guard: `services/workbench/loop/exec.py` (targeted: 30-60 + symbol greps at 46/86-96/166-250/262-266), `services/workbench/loop/surface.py` (1-60, 295-321), `services/workbench/workbench.py` (6756-7000, 7425-7455, 1690-1744, 5199/5284-5301 via grep), `services/workbench/permissions.py` (1-100), `services/workbench/subagent.py` (60-190, 355-475, 1380-1459).
- MCP routers/doors: `routers/mcp.py` (1-120 + route greps), `routers/harness_mcp.py` (full), `lib/gateway_auth.py` (full), `lib/local_api_auth.py` (full), `lib/local_api_guard.py` (1-80).
- Sandbox: `services/sandbox/hardline.py` (full), `policy.py` (full), `paths.py` (full), `runner.py` (full), `backends/container.py` (full).
- Secrets: `services/hooks/secret_guard.py` (full), `services/hooks/registry.py` (full), `services/hooks/lifecycle.py` (1-30), `services/secret_store.py` (1-120 + DPAPI greps), `services/service_connections.py` (420-540 + symbol greps).
- Providers/routing: `providers/clients/base.py` (full), `clients/openai.py` (full), `clients/anthropic.py` (full), `clients/rate_gate.py` (full), `providers/api_format.py` (full), `providers/model_resolver.py` (full), `providers/route_resolver.py` (full), `providers/resolver.py` (full), `services/workbench/providers.py` (470-590), `services/model_service.py` (400-519), `services/alias_service.py` (1-60), `services/cost_estimator.py` (full), `services/fallback_service.py` (full), `routers/proxy.py` (1-300), `routers/brain_config.py` (600-679).
- Local models: `services/llama_runtime.py` (1-90), `services/hf_hub.py` (1-50), `services/hf_downloads.py` (1-110), `services/comfyui.py` (1-40).
- Runtime data: `data/mcp-servers.json` (full; contains a live-looking secret — redacted in this report).

Skipped, with reasons:
- `frontend/desktop/**` — out of scope (backend audit); the Tauri side of the token doors was verified via the backend docstrings only.
- Most of the 60+ routers (`routers/`) — only the area-relevant ingress (mcp, harness_mcp, proxy, brain_config, browser import) were read; remaining routers are CRUD over services already covered.
- `adapters/openai.py` / `adapters/anthropic.py` full proxy adapters — verified via targeted greps (`parse_tool_arguments` call sites, `execute_managed_proxy_tool` routing); full stream-aggregation review was out of budget.
- `services/workbench/workbench.py` bulk (7,683 lines) — read the guard/approval/disclosure regions; the remaining turn-loop machinery belongs to the harness area audit.
- `services/alias_mapping_service.py` body, `hf_downloads.py` tail, `llama_runtime.py` tail — inventoried and symbol-mapped only; no findings suspected there.
- No tests were run and no processes started (read-only mandate); the repo's own claim of gate coverage (`check:tool-registration` in `package.json:29` + `scripts/gates.mjs:63`) was verified by inspection only.

## Open questions for the owner

1. Should the live `GOCSPX-…` client secret in `data/mcp-servers.json` be rotated and migrated to a secret-store reference now that `${VAR}` resolution exists at spawn time?
2. Is the missing `require_local_token` on MCP-server create/update/delete intentional (localhost trust model), or should the mutating routes match start/stop?
3. Should `tool_call`'s no-session raw-dispatch fallback (`tool_bridges.py:212`) fail closed for mutating tools, the same way the guard-exception path already does?
4. Do you want `GITHUB_TOKEN`/`SLACK_BOT_TOKEN` removed from `os.environ` in favor of use-time resolution, and the outbound scrub's env scan widened to token-class names?
5. Is the conservative "most expensive known model" pricing fallback acceptable for unpriced models, or should estimates return "unknown" instead?
