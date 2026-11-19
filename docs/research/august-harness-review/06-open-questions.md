# Open questions — decisions that need the owner

These are the questions the audits could not answer from code. Each is phrased as a
decision with the trade stated, and each names where it lands. Questions marked
**(blocking)** should be answered before the related proposal is implemented.

---

## Security

### Q1. The live Google OAuth secret in `data/mcp-servers.json` — rotate now? **(blocking)**
`GOCSPX-…` is in plaintext in the untracked data dir and is outside the outbound
scrub's collection set. Rotating is free; migrating the env map to `${NAME}`
references is a small change (the resolution door already exists). The only reason
not to do both immediately is if a third-party MCP server genuinely needs the literal
value in its env.

### Q2. Are `GITHUB_TOKEN` / `SLACK_BOT_TOKEN` in `os.environ` load-bearing?
`services/service_connections.py:493, 516` exports them so MCP children and other
subprocesses inherit them. If use-time resolution is acceptable, they can come out
of the environment entirely; if not, the scrub must widen to token-class names. Which?

### Q3. Is the localhost trust model intentional for MCP-server registration?
`create`/`update`/`delete` lack the per-install token that `start`/`stop` require.
If the answer is "localhost is trusted", the asymmetry should be written down; if not,
add the token (P0-8).

---

## Lifecycle / data

### Q4. Should daemons survive a restart at all? **(blocking for P0-6)**
If yes: persist terminal status on natural exit (P0-6). If no: delete the durable
store entirely — `daemons.json`, the `status` column, the `expires_at` string math
and `rehydrate_from_db` (78 lines) all go, and the defect disappears with them. The
current behaviour is a third option nobody chose: a store that mostly works.

### Q5. Should teardown failures be silent?
14 of 16 teardown handlers in `main.py` are a bare `pass`. Converting them to logged
swallows is mechanical, but it will surface failures nobody has seen since the writes
became durable. Do you want them found, or is the silence deliberate?

### Q6. Is `deferred_writes.py` a delete or a keep?
Retired no-op with live importers in the transcript domain, and a comment that now
lies about preventing telemetry loss.

### Q7. The mobile decision — second client or second August? **(blocking for the mobile work)**
`docs/ROADMAP.md:101-107` says the decision is open. Today's WebView shell silently
answers "second client of the desktop's own backend" without anyone deciding, and the
phone re-pairs after every app kill because `expo-secure-store` needs an owner. Which
is it, and may `frontend/mobile` take `expo-secure-store`?

---

## Product surface

### Q8. `subagentFanout`: where should the receipt live?
The backend carries `fanoutId` + per-lane detail. Options: a muted line in the
subagent lane, a transient toast, or only in the Runs/event-log surface. (The
frontend currently drops it, which is not one of the options.)

### Q9. Settings depth: is two-thirds-hidden the intended steady state?
The registry now has 31 of 46 sections `tier: 'hidden'` (docs say 26 of 44). Either
re-expand the basic tier or accept the number and fix the docs.

### Q10. Group across a turn boundary?
Still open (`docs/ROADMAP.md`): a read, an answer, and another read is two groups
today. Grouping across an answer would put unrelated work under one line and sum two
elapsed times that never ran together.

### Q11. Is `answered via {model}` meant to stay?
It is the only place naming the answering model, kept deliberately as muted tier-2
text. Confirm, or move it into a turn-provenance chip.

### Q12. Turn Limits: keep the Save button or commit on blur?
The reference's panel has no Save because its values commit as they change. August's
needs a draft flush on unmount, which is a rewrite of a save path — owner's call.

---

## Memory / skills

### Q13. Remove the M-7 disuse machinery now, or keep it until a ranked surface returns?
The columns and config keys can stay either way; the ~90 lines of scoring cannot ever
fire.

### Q14. Is a 60-second synchronous auto-review acceptable on the save path?
The fail-open design is right, but a slow reviewer stalls the save-chip write. Drop
the default to 15–20 s, or file first and review behind the write?

### Q15. Subagent memory reads: keep them blocked?
The deny set blocks `list_facts` too — stricter than "read-only". Opening reads is a
one-line deny-set change and an owner ruling (`docs/specs/memory-and-skills.md` §5).

### Q16. Save-chip: measure, then keep or remove?
Usage is still unmeasured (spec §6.7). If it is near zero, remove the chip and keep
the receipt + Settings undo rather than carrying new UI for a low-frequency action.

### Q17. Is the memory backup scope still right?
The archive covers the brain record only; `transcripts/` and `telemetry/` are excluded
by owner decision. Confirm, or widen it.

---

## Release / process

### Q18. Should tag-push releases also go through the draft gate?
Today a `v*` tag publishes live immediately; only `workflow_dispatch` drafts. For a
sole-maintainer workflow that may be the right trade — but it should be a decision,
not an accident of the workflow file.

### Q19. Is the MSI target load-bearing for any deployment?
The entire update pipeline is NSIS-only; the MSI is published and never consumed by
the updater. Drop it, or keep it for enterprise?

### Q20. Who owns the post-update relaunch — installer or waiter?
The code (`hooks.nsh:171-193`) and the docs (`product.md:59-63`) disagree, and the
installer's relaunch is currently dead. Pick one, align both, then fix the binary name.

### Q21. When does the packaged NSIS matrix get its first real run?
It is the only end-to-end check of the update path and has never been run. Parts of it
(binary name, marker paths, waiter constants) can be promoted into static gates first.

### Q22. May `gates:mobile` require a live backend?
`npm test -w frontend/mobile` needs a backend on `127.0.0.1:8085`, so `gates:mobile`
fails on a clean checkout for a reason unrelated to the diff. Split the static half from
the live half, or accept the dependency?

---

## Framing

### Q23. Is the product "minimalist" about features or about surface area?
Several proposals above add a capability (approval grants, compaction observability,
skill diagnostics) and several remove one (dead machinery, a duplicate route, an
unused installer target). The consistent rule that came out of the audit: **August's
minimalism is about what the user must read and click, not about what the harness is
allowed to do.** Confirm that framing, because it decides whether P1-6 and P1-11 are
"clutter" or "capability".

### Q24. The docs-drift register — should docs be gated on the change that closes a gap?
Seven items on the owner's open-gaps list are already fixed; two live defects appear on
no list. A `check:docs` rule that fails when a closed item is still listed would keep the
register trustworthy. Worth it, or is the register meant to be a snapshot?