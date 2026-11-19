# Aider (Aider-AI/aider) — harness dive for August

Repo: `C:/Users/rober/AppData/Local/Temp/august-review/repos/aider` (691 files, Python,
Apache-2.0). Read-only. Method: README + `aider/` tree + the key modules' structure and
public surface (`coders/base_coder.py`, `coders/*_coder.py`, `repomap.py`, `history.py`,
`models.py`, `commands.py`, `io.py`, `onboarding.py`, `gui.py`). Aider is a *chat-to-code*
agent, not a general workbench: it has no shell tool loop, no sandbox, no memory store, no
subagents. What transfers is its **repo-map + edit-format + benchmark** discipline, and its
**multi-coder architecture**.

## 1. Startup flow
`aider` / `python -m aider` parses args (`args.py`), resolves the model
(`models.py`, `openrouter.py`), loads `CONVENTIONS.md`/`AIDER.md` if present, builds the
repo map, then enters an interactive loop or a one-shot (`--message`). Launch modes:
interactive, `--message`/`-m` one-shot, `--gui` (the Streamlit-ish `gui.py`),
`--browser`, and a `--lint`/`--test` command hook. There is no daemon, no server, no
persistence layer beyond `.aider.chat.history.md` and `.aider.input.history`.

## 2. UI/UX patterns
Terminal-only (plus a `gui.py` browser front-end). The interaction is a **prompt/response
pair per turn**, with a persistent repo-map footer showing the token cost of the map and
the current context size. `/commands` are slash-style; `/undo` reverts the last commit;
`/diff` shows only the changes the model made. Error copy is terse and names the fix
(`/add <file>` when the model needs a file it cannot see).

## 3. Memory model
**No long-term memory.** Two markdown files: `.aider.chat.history.md` (the conversation)
and `.aider.input.history` (the user's prompts). Cross-session continuity is the repo map
plus the chat history file; there is no fact store, no profile lane, no recall. A
`CONVENTIONS.md` / `AIDER.md` in the repo is loaded as instructions — the same trust class
as `AGENTS.md`.

## 4. Skills / plugins
No skill system, no plugin system. Extension points are: custom edit formats
(`--edit-format`), custom commands (`commands.py`), and the `architect`/`editor` mode
(two models: one proposes, one implements).

## 5. Tool + permission model
No tool-calling loop at all — aider's "tools" are the *edit formats* and the shell-out
commands (`/run`, `/test`, `/lint`). The model proposes edits as text in a structured
format (`diff`, `diff-fenced`, `whole`, `udiff`, `editor-diff`, `editor-whole`); aider
parses them, applies them to disk, and runs tests/lint. There is **no permission gate** —
aider runs with the user's own permissions and asks only for git operations (commit/undo)
and for `--yes-always`. The safety model is "show the diff before applying", not "gate the
tool".

## 6. Sandboxing
None. Aider does not run model-authored shell.

## 7. Context management / compaction
- **Repo map** (`repomap.py`, 867 lines): a tree-sitter-parsed, PageRank-ranked graph of
  the repo's symbols, rendered into a *bounded* text map that fits a token budget. This is
  the single most transferable idea in aider: instead of dumping files, it sends a ranked
  structural summary, and re-ranks per query (files named in the chat get boosted).
- **Chat history compression** (`history.py`): `ChatSummary` tokenizes the history, and
  when it exceeds a budget it **summarizes the older portion** (`summarize_all`) with a
  model call, keeping the recent turns verbatim and the summary as a leading block. The
  summary is itself bounded (`max_tokens=1024`).

## 8. Subagent design
None in the core loop. The `architect`/`editor` mode is the nearest analogue: a stronger
model (`--architect`) proposes a solution in prose, then the editor model implements it
from the codebase — two calls, one turn, with the architect's answer as context for the
editor.

## 9. Model / provider handling
`models.py` (1,338 lines) is a **static, curated model registry**: name, context window,
max output, input/output price, whether it supports `weak`/`editor`/`architect` roles,
and the edit format each model is known to work with. It works with any OpenAI-compatible
or Anthropic endpoint (`--openai-api-base`, `--anthropic-api-key`), and with OpenRouter
first-class. There is no wire-format override per model beyond the format list; the
registry is the source of truth, and an unknown model falls back to conservative defaults.

## 10. Update mechanism
`pipx`/`pip`/`brew`; `aider --version` prints and there is a `--check-update` /
`--upgrade` path. No in-app updater, no signature verification, no staged rollout.

## 11. Config / settings
`.aider.conf.yml` (project or home), `.env` for keys, and ~200 CLI flags
(`--model`, `--edit-format`, `--auto-commits`, `--lint`, `--test`, `--map-tokens`,
`--cache-prompts`, …). Precedence: CLI > `.aider.conf.yml` (cwd) > `~/.aider.conf.yml` >
defaults. `AIDER_OPTS` env var carries extra args.

## 12. Error recovery
- Edit-format parse failures are **returned to the model** with the parser's own error and
  a reminder of the format, and the model retries (usually successfully).
- `--test` / `--lint` failures are fed back as the next user turn, so the model iterates.
- `/undo` reverts the last commit; `--no-auto-commits` disables the commit step.
- No compaction retry, no orphan repair — there is no tool-call pairing to orphan.

## 13. Clever / notably bad
**Clever:** the repo map (ranked structural context, per-query re-rank, hard token
budget) is the cheapest high-signal context mechanism in any of the studied harnesses;
the **edit-format** abstraction (the model emits a documented text shape, aider parses and
applies it) means the model never needs tool-calling — which is why aider works with weak
local models that cannot do native tool calls; the benchmark harness (see below) turns
"which edit format works with which model" into a measurable fact.

**Notably bad / not applicable:** no tool loop means no sandbox story, no permission story,
no subagent story; the static model registry rots (models change faster than the registry);
`.aider.chat.history.md` is a single untracked file, not a store; and the CLI-flag surface
is enormous for a "minimalist" tool.

## The benchmark discipline (worth stealing)
`benchmark/` runs aider against SWE-bench-style exercises and reports pass rates per
model × edit-format. The *shape* is the lesson: aider proves which configuration works by
running a fixed suite and reporting a table, rather than by reasoning. August has
`npm run eval:harness` and `eval:ablation` (the ablation design is stronger than aider's
for "did the harness change the outcome"), but aider's "model × configuration grid" is the
missing half — it answers "which model should this surface use?" with evidence.

## Ideas worth stealing for August

1. **A bounded, ranked structural repo map** (`repomap.py`) — for coding turns, a
   tree-sitter/PageRank summary of the workspace is cheaper and more useful than raw file
   dumps. August's `analyze_media`/workspace tools could gain a `repo_map` door; the
   budget and re-rank rules are the design. **VERIFIED (structure)**
2. **Edit formats as a first-class, model-specific choice** — a model that cannot do
   native tool calls can still edit via a documented text shape. August's `write_file`/
   `edit_file` assume tool calling; a `diff`-format fallback would let weak local models
   contribute. **VERIFIED**
3. **A model × configuration benchmark grid** — August's ablation runs one request twice;
   aider runs a suite across models × edit formats and reports a table. Adopt the grid
   shape for "which model should drive which surface". **VERIFIED (structure)**
4. **Chat-history summarization with a hard token cap and a leading summary block**
   (`history.py`) — August's compaction already does this shape; the transferable detail is
   that the summary is a *bounded, prepended block* rather than a replacement of the whole
   history. **VERIFIED**
5. **Two-role architect/editor split** — a stronger model plans in prose, a cheaper model
   implements. August's `chat`/`agent`/`code`/`orchestrator` modes are close; the missing
   piece is an explicit "plan in prose, then implement" pair within one turn. **VERIFIED**
6. **`.aider.conf.yml` precedence written as a rule** (CLI > project > home > defaults) —
   August's config has layers but no single stated precedence; write it down in
   `CONFIGURATION.md`. **VERIFIED**
7. **A test/lint command hook that feeds failures back as the next turn** (`--test`,
   `--lint`) — August's `run_command` surfaces exit codes; wiring the *test* result back as
   a structured nudge is the missing loop. **VERIFIED**

## Anti-patterns to avoid
- **A static model registry as the source of truth** (`models.py`) — it rots. August's
  "catalogs come from providers, never a hardcoded list" is the right rule; keep it.
- **No permission story at all** — aider runs with the user's permissions and only shows
  the diff. Fine for a CLI tool, wrong for a product that ships an installer.
- **200+ CLI flags with no settings UI** — the opposite of August's minimalist posture.
- **`.aider.chat.history.md` as the only durable record** — an untracked file per repo with
  no store, no undo, no schema.

## Not applicable to August
- The whole chat-to-code loop (no shell, no browser, no MCP, no memory).
- `benchmark/` as shipped (SWE-bench harness; August's eval scripts already cover the
  ablation shape).
- `--gui` / `--browser` front-ends.
- `pipx`/`brew` install + `--upgrade` (August has a better updater).

## Coverage ledger
**Read:** `README.md`, `HISTORY.md` (head), `aider/` tree listing, `coders/` (all coder
classes enumerated), `coders/base_coder.py` (structure + public surface), `history.py`
(full), `models.py` (structure + role/price/format fields), `repomap.py` (structure +
ranking/budget functions), `commands.py`, `prompts.py`, `io.py`, `onboarding.py`,
`gui.py` (head).
**Skipped, with reason:** `benchmark/` internals (the harness shape was taken from the
README + directory structure); `tests/` (the repo has a large test suite; it is the one
harness here where the suite is the interesting artifact, but running or reading it is out
of scope for a read-only static dive); `requirements/` (pins); the full bodies of
`base_coder.py` (2,485 ln) and `repomap.py` (867 ln) — aider's contribution is
architectural, and the architecture is fully described by the module structure and the
key functions. **No source-level claim about aider's runtime behaviour was verified by
execution; everything here is VERIFIED-as-read or INFERRED-as-structure.**