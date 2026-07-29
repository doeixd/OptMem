# Changelog

## 2.0.3 — 2026-07-29

- `--date` accepts a timestamp: `YYYY-MM-DDTHH:MM[:SS]`, no timezone offset
  (ISO order is lexicographic order, and an offset would break it). Bare
  dates stay the compact default. Chronology is enforced by day — a bare
  date and a timestamped moment coexist on one day, so time of day is
  informational — and import, deep verification, and fuzzy recall all accept
  timestamped records.
- No show/read hook, deliberately: write hooks exist because writes happen
  inside the tool where a pipe cannot intervene, while reading is already
  composable at the shell (`memo recall ... | your-filter`), and a display
  hook could corrupt the wake protocol agents depend on verbatim.

## 2.0.2 — 2026-07-29

- `note`, `amend`, and `retract` accept `--date YYYY-MM-DD` to backfill an
  entry's day — a real date, not in the future, and not before the newest
  memory, so the log stays chronologically ordered and import/verification
  invariants hold.
- Optional hooks, named by environment variables so a synced store can never
  execute code: `OPTMEM_HOOK_PRE` rewrites or refuses a memory before it is
  written (stdin: candidate line; stdout: replacement; nonzero exit:
  refusal), `OPTMEM_HOOK_POST` observes the record just written and warns —
  never unwrites — on failure. Neither runs for `import` or `nap`.
  `memo doctor` lists active hooks.
- Documented the store-widening recipe end to end in the README: export,
  move the old store directory aside (the one sanctioned manual touch, as a
  migration backup), recreate empty, set widths, import, recompress.

## 2.0.1 — 2026-07-29

### Configurable sizes

- `RAW_MAX` is now a per-store knob: how many raw memories one compression
  prompt shows before a block merges from its two half summaries (default 16,
  range 2–256). A reading preference — change it any time.
- `LOG_REC` and `TREE_REC` are per-store knobs too, which is how a store can
  hold entries longer than 280 bytes (`ENTRY_CHARS` is capped at
  `min(TREE_REC - 8, LOG_REC - 40)`). They are physical record widths:
  `config` refuses to change them once the store holds records — export,
  create a fresh store with the new widths, and import there. Cross-store
  status lines (doctor) read each store's own width leniently.

### Workflow

- `--fit` now also works on `amend` and `retract`, computed against the bytes
  that actually remain after the `Amends #a-#b: ` reference prefix — closing
  the rejection loop exactly where the budget is tightest.
- `show <lo>-<hi>` opens a summary block: its summary line, then the later
  records that supersede it. Blocks are now symmetric as amend targets and as
  questions.
- `memo doctor` reports the active session tag and its source
  (`OPTMEM_SESSION`, harness process, terminal, or none), making silent
  untagged fallbacks diagnosable.

### Provenance robustness

- Terminal multiplexer and remote-login daemons (`tmux`, `screen`, `sshd`)
  are treated as shared, not stable, ancestors: stopping the ancestry walk at
  a tmux server would hand every parallel pane the same tag. When only shared
  ancestors exist, the controlling terminal identifies the session instead;
  with neither, entries are written untagged rather than falsely identical.
- Declared ctypes argument types for the Windows process APIs, so
  pointer-sized handles are no longer at risk of truncation silently
  producing untagged entries.

### Documentation

- README: human-facing provenance and block-supersession sections, including
  the honest limits — tags are cooperative claims, not authenticated
  identities; legacy text beginning `@word ` reads as tagged; import
  tolerates non-block `#a-#b` ranges from foreign histories.
- AGENT_SETUP.md and WINDOWS.md now cover `--fit`, block supersession,
  session tags, and the PowerShell recall-quoting rule.

## 2.0.0 — 2026-07-29

### Provenance

- Every entry written by `note`, `amend`, and `retract` now carries an opaque
  session tag inside its payload (`#id date @tag text`). `OPTMEM_SESSION`
  names the tag explicitly; otherwise it is derived from the harness process
  that owns the session. Tags are visible in `wake`, `recall`, `zoom`, `show`,
  and nap prompts, so compressors and readers can weight another session's
  testimony correctly. Tagging is best effort: untagged v1 stores read back
  unchanged, `import` never invents tags, and a tag is dropped rather than
  refused when a record would overflow.

### Supersession of compressed history

- `amend` and `retract` accept a summary-block target: `amend <lo>-<hi>`
  appends `Amends #lo-#hi: …`, superseding a range whose authoritative
  statement already lives in a compressed summary line. `show <id>` lists
  block supersessions covering that raw memory as later references. Import
  and deep verification validate range references.

### Workflow

- Over-long entries now report the overage and the exact suffix to cut;
  `note --fit` trims at a word boundary and prints what was dropped.
- Bare `nap` is the documented primary form; the wrong-block error names it
  as the recovery when a parallel session settles a block first.
- `recall` rejoins a shell-split multi-word query instead of rejecting it,
  and documents the PowerShell quoting rule in its usage error.
- The final `wake` part prints a frontier trailer naming how many raw
  memories the bounded view elided and the largest summarized block to zoom.

## 1.1.1 — 2026-07-27

### Agent guidance

- Updated the managed agent instruction block and README reference to describe
  automatic project selection, `MEMORY_DIR` overrides, append-first redaction
  semantics, and case-insensitive regex recall accurately.
- Clarified that agents must finish paginated wake output and every printed
  compression before continuing, including compression printed after the
  awake line.
- Made the parent agent explicitly responsible for recording durable outcomes
  returned by subagents.

## 1.1.0 — 2026-07-27

### Automatic project scope

- Added read-only `memo scope` diagnostics for the selected project, store,
  detection source, remote, and compatibility layout.
- Hosted projects now use their complete `host/namespace/repository` identity
  and collision-resistant store keys instead of collapsing to `owner/repo`.
- Added deterministic remote selection beyond `origin`: tracked and sole
  remotes are detected automatically, with `OPTMEM_REMOTE` for intentional
  multi-remote selection.
- Claimed legacy host/namespace collisions now receive isolated stores instead
  of sharing history; matching legacy stores remain in place without migration.
- Projects without a usable remote gain a portable identity marker on first
  memory use, preserving scope across directory and filesystem moves. Existing
  path-based stores are linked without moving or rewriting their memories.

### Setup and guidance

- Clarified that `memo setup` teaches agents to use the CLI by updating
  existing `AGENTS.md` and `CLAUDE.md` files; creating missing files remains
  an explicit `--create` choice.
- Reworked `memo --help` around connection, retrieval, maintenance, optional
  integrations, scope precedence, and copyable examples.

## 1.0.1 — 2026-07-27

### Wake reliability

- Fixed multi-page project wake continuations restarting global memory after
  the global-to-project handoff.
- Routed the paginated handoff through the normal store initialization path so
  symlink checks, project identity recording, and later hardening always apply.
- Added an end-to-end regression that follows every printed continuation
  through multi-page global and project memories to one final awake state.

## 1.0.0 — 2026-07-27

Initial stable release of the project-scoped OptMem fork.

### Memory lifecycle

- Added append-only amendments and retractions over stable memory IDs.
- Added direct `show` lookup with bounded later-reference discovery.
- Renamed summary repair to `resummarize`; `forget` remains an alias.
- Added explicit, forced redaction with summary invalidation and QMD cleanup.
- Added portable streaming export and empty-store-only import with dry-run
  validation.

### Retrieval and compression

- Added optional resident FFF fuzzy recall.
- Added optional, isolated QMD semantic recall with lazy 16-memory projection
  segments, fast mode, explicit synchronization, and opt-in fallback.
- Added bounded tree-depth navigation and recall context/limit controls.
- Added batched compression with atomic, preflighted application.

### Installation and operations

- Added native PowerShell installation, Windows locking, PATH setup, and shell
  completions for Bash, Zsh, Fish, and PowerShell.
- Added upgrade, uninstall, version, deep verification, and project-scope
  collision diagnostics.
- Added safe managed setup for `AGENTS.md` and `CLAUDE.md`.
- Release installers and upgrades now consume tagged GitHub release assets.
