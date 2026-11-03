# Status — the current round

Last updated: 2026-11-03 · App version: **0.20.0**

This is the committed handoff. Read it before starting work; update it at the
end of a round. Keep it to one page: what shipped, what is next, what is
waiting on the owner, and how to resume.

## This round — local models, GGUFs, Hugging Face, ComfyUI

- **Local servers are detected and one click from usable.** A keyless
  provider whose base URL is loopback now resolves everywhere a key was
  required (six gates were fixed: credentials resolve, list_available,
  activeProvider, workbench model hints, provider details, and model
  discovery). **Settings → Local Models** scans loopback (Ollama, LM Studio,
  llama.cpp, Jan, GPT4All, ComfyUI — by response shape, not port) and adds
  servers through the ordinary provider-create path.
- **GGUFs run inside August.** An on-demand llama.cpp runtime downloads into
  `{dataDir}/runtimes` (never the installer payload), every asset verified
  against its pinned SHA256, with CUDA 12.4 / CUDA 13.4 / Vulkan / CPU
  variants picked from `nvidia-smi` and a manual override. Serving a GGUF
  spawns `llama-server`, registers a 0.0-priced provider, and its models
  appear in the composer; servers are killed on shutdown and orphan-swept at
  boot.
- **Models come from Hugging Face.** GGUF search, per-quant file lists with
  exact sizes, resumable downloads into `{dataDir}/models/hf` preserving
  quant subfolders, free-disk gating, and serve-on-finish — progress polled,
  no new SSE frames.
- **ComfyUI is the image engine.** Connection + checkpoints + custom
  API-format workflows in **Settings → ComfyUI**; the `generate_image` tool
  is registered only while the connection is enabled (honest surfaces) and
  writes into the workspace through the artifact-tools door.
- **Verified on the real machine**: the runtime installed (Vulkan
  auto-selected after the 616.64 nvidia-smi banner fix; CUDA 13.4 variant
  downloading), the user's own `LFM3-1.7B-Q8_0.gguf` served, auto-registered
  with a clean model id, and answered August's Test probe — `Connected!`
  in 889 ms through the ordinary OpenAI-compatible chat path.
- **Follow-ups in the same round**: thinking blocks collapse to ONE line
  and never auto-expand; SimulIDE `.sim/.sim1/.sim2` files parse, describe
  (analyze_media), and render as schematics in the Circuit pane; chat
  attachments are uncapped and carry original file bytes (binary + from-path
  doors); Hugging Face search browses ALL models with a GGUF-only filter;
  the loopback keyless exemption now reaches the chat callers and proxy
  adapters (the Test button on a local model works).
- **SimulIDE circuits simulate in August, not just render.** A digital
  engine (nets from wires/tunnels/Node junctions, a component library for
  gates, sources, 7-segment displays, and the common 74-series subcircuits)
  reproduced the user's calculator deck exactly — idle 0, key-5 pressed 5,
  5−3=2 in subtract mode, 4-bit wrap on overflow — and the Circuit pane
  gained source toggles plus a live render (wires colored by logic level,
  DIP boxes on their real pin positions, real gate silhouettes). Analog
  parts and MCUs stay honestly unsimulated in the notes.
- **The shared space ships (from the rerun study).** One folder every
  session and bot can read and write — `{dataDir}/shared/` with
  `shared_list/read/write/search`, jailed through the sandbox door — is the
  handoff surface between chats and bots that separate workspaces lacked.
- **The Foreman and guided setup ship (also rerun patterns).** A
  boot-reconciled `Foreman` bot (persona rewritten from code each start, so
  every update ships today's Foreman) holds the one non-human authority: pin
  model/provider on teammates it builds through `create_agent`. A bot born
  with `setup_required` carries a pending walkthrough in its chat and an
  `install_complete` tool — advertised only while pending, fail-closed on
  re-call, plan-blocked and subagent-blocked — that closes the setup and
  activates the routines parked for it (`create_routine paused: true`).
- **The last two rerun patterns shipped.** `request_secret` puts a masked
  form on the tool-call card — the value goes straight into the encrypted
  store (`chatSecrets.<NAME>`), the model learns only the NAME and status,
  and MCP server env resolves `${NAME}` against it at launch. And the
  data-dir migration ladder is live: numbered, re-runnable steps applied
  before any store opens, version marker at `{dataDir}/data-version.json`,
  a failed step refuses the boot.
- Still on the human list: a real ComfyUI generation, the CUDA 12.4 variant
  on a 12-series driver, and the desktop-app pass (the dev-backend tests ran
  on :8091 against the real data dir).
- Previous round below; the self-edit loop and the rename shipped there.

## The round before — the app edits its own source, and is renamed

- **The self-edit loop ships.** The model edits August's source only inside a
  user-local clone (`{appData}/clone`), and with self-edit on that clone *is*
  the runtime — a restart onto pre-edit source would make "apply" a lie. Every
  clone write is journaled with its pre-edit bytes and undone at boot if it was
  never applied; unattended runs cannot write the clone at all; the model can
  never push to a remote in any mode
  ([agent/security.md](agent/security.md)).
- **The apply door is judged by a checklist the clone cannot edit.** The gate
  harness is embedded in the shell (`include_str!`), not read from the tree
  under test, and it exits with its own verdict. Settings → Self-edit is the
  only way an edit becomes live, over a Tauri IPC command that is deliberately
  not a model tool.
- **0.18.26 shipped a door that could not open**: the smoke gate was spawned
  without the clone root its containment check needs, so *every* apply failed on
  `root=None`. Fixed and re-verified in the running app; **0.18.27 is the release
  that carries that fix under the old identity**, so an installed 0.18.26 can
  self-update onto a working apply door.
- **An update is deferred while self-edit is on** — re-staging
  `backend-runtime` under a clone-preferring pin would report the new version
  and execute the clone's older HEAD. Three doors refuse and each names the way
  out.
- **Renamed to augustHarness**, in four stages: env prefix and code-side
  identity, the repository and every URL that reads it, the shipped product name
  and artifact prefix, then the bundle identifier `com.august.harness`. Case is
  not style: human-facing `augustHarness`, toolchain-parsed `augustharness`, and
  the skill identifier `august-harness` (a directory name the model types).
  The old updater endpoint answers `301 → augustHarness`, verified, so installs
  already out there keep updating through the rename.
- **The identifier change moves the user's state exactly once, and proves it.**
  `migrateAppDataRoot` renames the data directory before the instance lock is
  taken, leaves a junction at the old path so anything still spelling it resolves
  to the same bytes, and compares a (file count, bytes) fingerprint before and
  after — a mismatch moves it back. It refuses rather than merges. Rehearsed
  against fabricated stores both directions, never the live one.
- `HARNESS_DESKTOP_APP_DATA` redirects the data root for **debug builds only**
  (an absolute path, ignored with a log if relative), which is what made the
  loop testable end to end without touching the real brain database.
- Not yet done, and owned by nobody: writes made through `run_command` or a
  warm kernel are still not journaled (only the file-tool door is armed); a
  committed marker has no revert path after boot; and `remote.origin.pushurl`
  locking does not stop `git push <explicit-url>`, so the push guarantee is a
  pattern guard plus a lock, not either alone.

## The round before that — the harness stops asking

- **Learned skills apply on their own.** `skillAutonomy` ships on for
  `skill_create`/`skill_patch`, the burn-in rail no longer deadlocks a fresh
  install, and the reviewer runs in the same pass instead of on the six-hour
  tick. Destructive kinds (delete, retire, archive, config) still wait for a
  human.
- **A Claude export moves in whole.** `POST /api/august/memory/import-claude`
  reads the folder or `.zip`, maps `profile.md`/`preferences.md` onto the
  always-in-context lanes and merges each bullet against `facts`
  ([memory.md](agent/memory.md)).
- **Repo instructions load without a consent click.** `.aug/skills` follows the
  `projectSkills` toggle like the repo's own `AGENTS.md`; the per-workspace
  skill-trust store, its routes and its banner are gone. Hook trust stays — a
  hook is unsandboxed shell. `list_skills` no longer cuts the trigger at 60
  chars.
- **Settings → Memory is the Claude-shaped list now.** The rail's Memory door
  mounts the grouped page (You / Topics / Areas / Notes / Rules) instead of the
  Library table, a shared hook line prints once per group rather than on every
  row, and `memoryReview` + `memorySensitiveTopics` moved to the top of the page
  (out of the behavior group, so neither is edited from two surfaces). The table
  stays behind the hidden `memory-facts` door, and the Skills door is unchanged.
- Not yet done: memory **retirement** proposals still need a human, repo hooks
  still have no Settings door, and `august-bench --memory-state on|off` exists
  but has no recorded two-arm result for what the loop now writes by itself.

## Previous round — repository orientation

- `AGENTS.md` is a short contract (~120 lines) with a read-before-touching
  table; the detail moved to [`docs/agent/`](agent/README.md).
- Closed plans, dated audits, deep dives, benchmark captures, release notes and
  archive trees were deleted. [`docs/DOCUMENTATION.md`](DOCUMENTATION.md) is the
  one index; git history keeps the removed documents.
- Added [`docs/PRODUCT.md`](PRODUCT.md), this file, and
  [`docs/specs/memory-and-skills.md`](specs/memory-and-skills.md).
- CI is one reusable workflow; the release job calls it; one gate command set
  is shared by CI, release and developers. See
  [`docs/agent/workflow.md`](agent/workflow.md).
- Branch, commit and staging rules are in `CONTRIBUTING.md`. Two stale branches
  were deleted: `fix/user-row-attachments` (one unmerged commit) and the
  fully-merged `harness-skill-autonomy`.

## Recently shipped before this round

- **0.18.25** — commands that cannot run are no longer offered; failures that
  change state say so; a new chat no longer asks permission for something that
  loses nothing.
- **0.18.24–0.18.21** — the installer always brings the app back; a locked
  payload aborts instead of copying over it; one teardown per click; More opens
  right, reasoning reveals in full.
- **Memory and skills** — the 18-item memory/skills spec is ~95% built. The
  current state and the remaining items are in
  [`docs/specs/memory-and-skills.md`](specs/memory-and-skills.md).

## Next, in order

1. **Memory and skills UX.** The backlog is in the spec: make recall legible,
   show a skill's trigger/usage/history, surface why a skill is hidden, and
   finish the remaining inspection surfaces. This is the owner's priority.
2. **Verify the update flow on a packaged NSIS install — including the rename.**
   The matrix in [`UPDATE_VERIFICATION.md`](UPDATE_VERIFICATION.md) has never
   been run, and `0.18.26 → 0.19.0` is the transition it was never tested for:
   the bundle identifier, the binary name and the data root all change at once.
   0.19.0 is bumped and green on `master` but **not tagged** — the tag publishes
   it as the latest release, which an installed 0.18.2x would download and
   install without asking. The owner installs the renamed build by hand first.
3. **The mobile companion.** The decision below is unresolved, and it changes
   the size of the work by an order of magnitude. The Expo `package` is now
   `com.august.harness`, which makes it a separate install on a phone rather
   than an upgrade of the old one.
4. **Active plans** — the Signal Desk UI port, goal environments, and
   self-modification autonomy (see [`DOCUMENTATION.md`](DOCUMENTATION.md)
   §Active plans).
5. **DB retention doors (backlog, owner-noted 2026-10-30).** Four live tables
   have no cleanup path: `subagent_runs` (worst — no TTL and missed by both
   privacy wipes), `harness_jobs` (no TTL), `routing_evidence` (no cleanup,
   not in the privacy lists), `decision_ledger` (expired rows are only
   status-flipped, never deleted). Give each a retention/privacy door; none
   is scheduled.

## Open owner decisions

| Decision | The question | Where it lands |
|---|---|---|
| Mobile: second client or second August | Does the phone drive the desktop install over the network, or run its own backend and brain? | `ROADMAP.md` §NEXT, mobile |
| Group across a turn boundary | Should a read, an answer, and another read be one activity line? | `ROADMAP.md`, chat surface |
| Apply an update without a restart | The download is automatic; should an idle app restart itself? | `ROADMAP.md`, update flow |
| `/mcp/harness` by default | It is closed unless external access is enabled; an outer agent needs the key | `ROADMAP.md` |
| Subagent memory reads | Subagents currently read and recall but never write; opening reads wider is a product ruling | `docs/specs/memory-and-skills.md` |

## Resume

```bash
npm run dev:desktop                 # the product
cd backend-py && uv run ruff check . && uv run mypy app/
npm run check -w frontend/desktop
npm run gates:all                   # the static gate set
npm run e2e:smoke                   # starts the real app against a throwaway data dir
```

Two machine facts that are not in the tree. This checkout still lives at
`C:\Dev\august-proxy` — the directory rename to `augustHarness` is blocked while
an editor or a session holds the workspace root, and after it the backend venv
needs `uv sync` (its editable `.pth` records the absolute path). And
`origin` here carries a locked `pushurl`: a push needs the lock lifted by the
owner, which is the point of it.

The product goal is in [`PRODUCT.md`](PRODUCT.md); the contract is
[`AGENTS.md`](../AGENTS.md); the area guides are [`docs/agent/`](agent/README.md).
