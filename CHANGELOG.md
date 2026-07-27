# Changelog

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
