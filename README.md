# OptMem

Permanent, local memory for AI coding agents.

OptMem gives an agent continuity across sessions, compaction, models, and
vendors. Memories are local plain text records, immutable by default: one focused
store per project, plus one small global store for facts that follow you
everywhere. There is no account, server, background daemon, or required Python
package.

This fork builds on [VictorTaelin/OptMem](https://github.com/VictorTaelin/OptMem)
and adds project-scoped memory, native Windows support, optional FFF recall,
optional QMD semantic recall, and a polished agent/install workflow.
See the [changelog](CHANGELOG.md) for release history.

![how OptMem works](anim/optmem.gif)

## Installation

Prerequisite: Python 3.7 or newer. FFF-powered fuzzy recall is optional and
requires Python 3.10 or newer. QMD-powered semantic recall is separately
optional and requires Node.js 22 or newer only when enabled.

### Linux and macOS

```sh
curl -fsSL https://raw.githubusercontent.com/doeixd/OptMem/main/install.sh | sh
```

### Windows PowerShell

```powershell
irm https://raw.githubusercontent.com/doeixd/OptMem/main/install.ps1 | iex
```

The installer validates the download, installs the tool under `~/.optmem`,
adds the `memo` command to your user PATH, registers completion for your
shell, and creates the global store without touching existing memories. It
then prints a block between
`BEGIN OPTMEM AGENT INSTRUCTIONS` and
`END OPTMEM AGENT INSTRUCTIONS`. Open a new Unix shell after installation;
the PowerShell installer updates the current session immediately.

## Setup

From the project you want to connect, teach your agent how to use the `memo`
CLI, then verify the automatic project scope:

```sh
cd /path/to/project
memo setup
memo scope
memo doctor
```

On Windows:

```powershell
Set-Location C:\path\to\project
memo setup
memo scope
memo doctor
```

`memo setup` adds a managed OptMem instruction block to `AGENTS.md` and
`CLAUDE.md` when those files exist. The block explains how the agent should
use the CLI: when to wake memory, what belongs in a note, how project and
global scopes differ, and how to recall and compress memories. Missing files
are skipped by default; use `memo setup --create` to explicitly create them.
Existing files keep all of their other content. It is safe to run setup again:
current blocks are left byte-for-byte unchanged and older managed blocks are
updated in place.

To target another existing instruction file, pass it explicitly:

```sh
memo setup path/to/agent-rules.md
```

Add `--create` when that explicit file does not exist yet. `--no-create` is
also available when a script should state the safe default explicitly.

If you prefer manual setup, copy the block printed by the installer into your
agent's persistent instruction file. Existing hand-copied blocks are detected
and are never duplicated.

Start a new agent session inside the project after connecting the files.
Generated agent instructions still use the full executable path, so they work
even before a new shell picks up PATH changes.

`setup` manages instruction files only; it does not create or select a memory
store. Scope is detected when `memo` runs. `memo scope` is a read-only preview
of the project identity, selected store, and reason for the selection.

<details>
<summary>Review the installer before running it</summary>

Linux/macOS:

```sh
curl -fsSLo install-optmem.sh https://raw.githubusercontent.com/doeixd/OptMem/main/install.sh
less install-optmem.sh
sh install-optmem.sh
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/doeixd/OptMem/main/install.ps1 -OutFile install-optmem.ps1
Get-Content .\install-optmem.ps1
powershell -ExecutionPolicy Bypass -File .\install-optmem.ps1
```

</details>

## Use it with an agent

Once the generated block is in the agent's instructions, normal operation is
automatic:

1. At session start, the agent runs `memo wake` and reads global context
   followed by this project's context.
2. During work, it records durable decisions and discoveries with
   `memo note "..."`.
3. When an earlier memory changes, it uses `memo amend` or `memo retract`
   instead of silently contradicting history.
4. If a note requests a compression, the agent completes that `memo nap`
   before continuing.
5. When older information is needed, the agent uses exact or fuzzy `recall`,
   optional semantic recall, then `zoom` to inspect a summary in more detail.

Good project memory:

```text
The API client retries 429 responses with capped exponential backoff.
```

Good global memory:

```text
The user prefers concise status updates and PowerShell examples.
```

Do not record temporary progress, guesses, secrets, or facts already captured.
See [AGENT_SETUP.md](AGENT_SETUP.md) for setup patterns, a first-session
walkthrough, and guidance for subagents.

## The mental model

| Scope | How to address it | What belongs there |
|---|---|---|
| Current project | `memo note "..."` | Architecture, commands, decisions, failed approaches, repository-specific preferences |
| Global | `memo --global note "..."` | User preferences, machine/tooling facts, durable information true across unrelated repositories |
| Explicit override | Set `MEMORY_DIR` | Advanced use: pin every command to one chosen store and bypass project/global scoping |

Each store has an append-first raw log and a rebuildable binary tree of lossy
summaries. `wake` reads a bounded frontier with coarser summaries for older
history and finer detail toward the present; `recall` searches the full raw
log, while `zoom` opens a summary toward the entries beneath it. Compression
makes startup context smaller—it never deletes the raw memories.

Raw memories have stable `#IDs`, and later memories may cite those IDs to
anchor durable facts or decisions. Corrections are later events: `amend`
appends a replacement and `retract` appends that an older claim is no longer
authoritative. Only explicit user-directed redaction rewrites a payload.

When explicitly enabled for a scope, QMD gets a disposable Markdown
projection of the same raw log in fixed 16-memory segments. QMD chooses
relevant segments, but OptMem rereads the selected stable `#IDs` from
`LOG.txt` before displaying them. QMD never writes memories and does not
participate in `note`, `nap`, or `wake`.

`memo wake` is special: without `MEMORY_DIR`, it reads global memory first and
project memory second. Other commands target only one scope. Put `--global`
before the command—not after it.

Hosted Git projects use their full `host/namespace/repository` identity, so
worktrees and differently named checkouts share memory while different hosts
and deep GitLab-style namespaces remain isolated. OptMem prefers `origin`,
then the current branch's tracked remote, then a sole usable remote. Set
`OPTMEM_REMOTE=<name>` when a multi-remote checkout needs an explicit choice.

Without a usable remote, OptMem uses the Git root or current directory. On
first memory use it writes a tiny stable identity marker inside `.git` for a
Git checkout, or `.optmem-scope` for a non-Git directory. That lets the project
keep its memory when moved or renamed, including across filesystems. Read-only
commands such as `scope` and `doctor` never create the marker or a store.

## Commands

| Command | Purpose |
|---|---|
| `memo version` / `memo --version` | print the installed OptMem release |
| `memo init` | create the global memory and print the current agent instructions |
| `memo setup [--create\|--no-create] [FILE ...]` | teach agents to use the `memo` CLI by updating existing instruction files; defaults to `AGENTS.md` and `CLAUDE.md`, with creation opt-in |
| `memo completion <shell>` | print completion for Bash, Zsh, Fish, or PowerShell |
| `memo upgrade` | install the latest GitHub release, validate it, and refresh PATH/completion setup |
| `memo uninstall` | remove the command and shell integration while preserving every memory |
| `memo scope` | show the automatically detected project, store, remote/path source, and compatibility layout without creating anything |
| `memo doctor [--deep]` | explain setup and scope; optionally verify the raw log, tree, lifecycle references, and QMD projection state |
| `memo qmd enable` | explicitly enable optional QMD semantic recall for this scope |
| `memo qmd help` | explain the integration’s commands, isolation, and lazy behavior |
| `memo qmd status` | inspect the QMD executable, projection, collection, and embeddings |
| `memo qmd sync` | update the projection, index, and embeddings before they are needed |
| `memo qmd rebuild` | rebuild this scope's disposable projection and QMD collection |
| `memo qmd config [FALLBACK=on\|off]` | inspect or opt into semantic fallback after exact and FFF misses |
| `memo qmd disable [--purge]` | disable QMD; optionally remove its projection |
| `memo wake` | read both memories — global, then project; first command of every session |
| `memo note [--fit] [--date YYYY-MM-DD] "..."` | record one memory: one line, up to 280 UTF-8 bytes (project by default); `--fit` trims at a word boundary, `--date` backfills a past day (also on `amend`/`retract`) |
| `memo show <id\|lo-hi>` | show one canonical raw memory — or a summary block — and later records that reference it |
| `memo amend [--fit] <id\|lo-hi> "..."` | append a corrected replacement; a `lo-hi` block supersedes an already-compressed range |
| `memo retract [--fit] <id\|lo-hi> "<reason>"` | append that an earlier memory or summarized range is no longer authoritative |
| `memo nap` | answer the merges that came due |
| `memo nap --batch N` | print up to N independent compression jobs |
| `memo nap --apply <file>` | preflight and atomically apply TAB-separated batch summaries |
| `memo recall [--limit N] [--context N] <regex>` | search the complete raw log while controlling matches and neighboring entries |
| `memo recall --fuzzy [--limit N] [--context N] "<text>"` | typo-tolerant raw-memory search with optional FFF |
| `memo recall --semantic "<meaning>"` | meaning-based raw-memory recall with explicitly enabled QMD |
| `memo recall --semantic --fast "<meaning>"` | semantic recall without QMD reranking, useful for repeated related searches |
| `memo zoom [--depth N] <lo>-<hi>` | open one to six levels of a summary-tree node |
| `memo resummarize <lo>-<hi>` | drop a bad summary; the next nap rebuilds it (`forget` remains an alias) |
| `memo redact <id> --force` | permanently erase one sensitive payload, preserve its ID, and invalidate derived caches |
| `memo config [NAME=N]` | inspect or change the active store's sizes: reading budgets, merge granularity, and — while empty — record widths |
| `memo export [--with-ids] [file]` | write a portable history; IDs are omitted by default so the output can be imported |
| `memo import [--dry-run] <file>` | validate or restore `YYYY-MM-DD <text>` records into an empty store |
| `memo --help` | show the complete command overview |

Put `--global` before any command to reach the memory that follows you into
every project. Merges arrive one at a time, in the output of `note`. Nothing
ever runs in the background.

## Memory lifecycle

```sh
memo note "Mutation requests may be retried automatically."
memo show 42
memo amend 42 "Mutation requests are not retried; only idempotent reads are."
memo retract 42 "Obsolete after the HTTP client rewrite."
```

`amend` and `retract` are ordinary later memories:

```text
#81 2026-07-27 Amends #42: Mutation requests are not retried; only idempotent reads are.
#82 2026-07-27 Retracts #42: Obsolete after the HTTP client rewrite.
```

The original remains visible as history, but compression treats later
amendments, corrections, and retractions as authoritative. Agents may also
reference earlier `#IDs` in ordinary notes when a stable fact, decision, or
causal link benefits from an exact anchor.

Supersession outlives compression: once the authoritative statement lives in
a summary line `#a-b`, `memo amend a-b "..."` targets the whole block with
`Amends #a-#b: ...`, and `memo show` on the block or on any raw memory inside
it lists that supersession as a later reference. (`amend`/`retract` write
only aligned blocks; `import` also tolerates plain `#a-#b` ranges from
foreign histories.)

### Hooks

Two optional environment variables name commands that pre- or post-process
every memory written by `note`, `amend`, and `retract` (never `import` or
`nap`):

- `OPTMEM_HOOK_PRE` — receives the candidate line on stdin *before*
  validation; its stdout replaces the line, and a nonzero exit refuses the
  write entirely. A secret scanner, a normalizer, or a policy gate lives
  here. The refusal message is the hook's own stderr.
- `OPTMEM_HOOK_POST` — receives the exact record just written
  (`#id date @tag text`) on stdin. The memory is already durable, so a
  failing post hook prints a warning and never unwrites — use it for sync,
  notification, or external indexing.

Hooks are deliberately environment variables, not files inside the store: a
synced or imported store directory must never be able to execute code.
`memo doctor` lists any active hooks.

### Provenance

Every entry written by `note`, `amend`, and `retract` is stamped with an
opaque session tag inside its payload:

```text
#83 2026-07-29 @9845 The staging cluster pins Postgres 16.
```

Set `OPTMEM_SESSION` to choose the tag; otherwise one is derived from the
harness process that owns the session (or the controlling terminal), so
parallel agent sessions on one machine are distinguishable in `wake`,
`recall`, and the compression prompts. `memo doctor` reports the active tag
and where it came from. Three honest limits: tags are cooperative claims,
not authenticated identities — any process may set `OPTMEM_SESSION` to any
value; when no stable identity exists, entries are simply written untagged;
and a pre-2.0 entry whose text happened to begin `@word ` reads as if tagged.

An entry's date defaults to today. `--date YYYY-MM-DD` backfills a real past
day — useful when recording something learned earlier — but the log stays
chronologically ordered: the date may not be in the future and may not
precede the newest memory. To reference an out-of-order past event, name it
in the text instead.

`redact` is intentionally different and requires `--force`: it replaces the
payload with `[REDACTED BY USER]`, preserves the ID and date, invalidates
summaries, and rebuilds or invalidates optional QMD data. Use it only when text
must actually be erased—not for routine corrections.

Portable backups use the same format as import:

```sh
memo export memories.txt
memo import --dry-run memories.txt
memo import memories.txt       # destination must be empty
```

Default export omits IDs. Importing the records in order into an empty store
recreates the same IDs, so amendment and retraction references remain valid.
`--with-ids` is an inspection format and is not importable.

### Recall controls and tree depth

Recall always searches the complete raw log. Its options control only what is
returned:

```sh
memo recall --limit 5 "retry|backoff"
memo recall --context 2 "migration failed"
memo recall --fuzzy --limit 3 --context 1 "aproximate memry"
```

`--limit` keeps the newest exact matches or strongest fuzzy matches.
`--context` includes that many adjacent raw memories on each side, merges
overlapping windows, and marks actual matches with `>`.

Tree depth belongs to `zoom`, not recall:

```sh
memo zoom --depth 3 0-255
```

Depth defaults to one and is capped at six so one command cannot flood an
agent's context. Existing byte and line output limits still apply.

### Shell completion

The installer registers completion for the active shell and does not duplicate
profile entries when run again. After opening a new shell, type `memo` and
press Tab to complete commands, flags, configuration names, and setup/import
paths.

For a custom shell configuration, print the completion script directly:

```sh
memo completion bash
memo completion zsh
memo completion fish
memo completion powershell
```

### Optional fuzzy recall with FFF

OptMem has no required Python packages. On Python 3.10+, install
[FFF](https://github.com/dmtrKovalenko/fff) to add typo-tolerant recall:

```sh
python3 -m pip install fff-search
memo recall --fuzzy "aproximate memry"
```

Normal `recall <regex>` remains an exact, dependency-free scan. When it finds
nothing and FFF is installed, OptMem automatically retries fuzzily and ranks
the strongest matches first. FFF is instantiated only for that recall command;
OptMem remains a one-shot CLI, so this integration uses FFF for match quality,
not its long-lived warm-index performance.

### Optional semantic recall with QMD

[QMD](https://github.com/tobi/qmd) adds local BM25, vector retrieval, query
expansion, and reranking without becoming an OptMem dependency. Install it
only if you want meaning-based recall:

```sh
npm install -g @tobilu/qmd
memo qmd help
memo qmd enable
memo recall --semantic "Why did we stop retrying mutation requests?"
memo recall --semantic --fast "Where does the retry policy live?"
```

QMD currently requires Node.js 22 or newer. The first semantic recall runs
lazy indexing and may download QMD's local embedding, query-expansion, and
reranking models. Those models can require roughly 2 GB in total; later
unchanged recalls reuse the index and embeddings.

The integration is per OptMem scope. Enable project memory from that project;
use `memo --global qmd enable` separately for global memory. Both live in a
dedicated QMD index named `optmem`, with a collision-resistant collection per
store, so they do not enter the user's ordinary QMD knowledge base.

```sh
memo qmd status
memo qmd sync
memo qmd rebuild
memo qmd config FALLBACK=on
memo qmd disable
memo qmd disable --purge
```

`memo qmd sync` prepays the same lazy projection, update, and embedding work
that semantic recall would otherwise perform. Full semantic recall uses QMD's
hybrid retrieval and reranking; `--fast` keeps hybrid retrieval but skips the
reranker.

`FALLBACK=on` is deliberately opt-in and per scope. With it enabled, ordinary
`memo recall <regex>` tries exact recall, then FFF fuzzy recall, then QMD only
if both return nothing. Invalid regular expressions still fail immediately,
and explicit `--fuzzy` never escalates. Use `FALLBACK=off` to restore the
default. The setting is retained by `disable` and removed by `--purge`, but it
is inactive whenever QMD itself is disabled.

`disable` unregisters the collection but keeps the disposable Markdown
projection for a cheap re-enable. `--purge` removes the projection too.
Neither command changes `LOG.txt`, `TREE/`, or any memory. QMD synchronization
is lazy: `note`, `nap`, and `wake` never launch Node or generate projection
files. When QMD is enabled, the final `wake` page only adds a small capability
line; it does not invoke QMD. If semantic recall fails, OptMem reports that
failure explicitly while exact and FFF recall remain available.

## Why split memory

A single log is one identity, and wake spends its reading budget on the
present. That is right for one continuous workstream and wrong for several:
interleaved projects make each one's detail decay while work happens
elsewhere, so an old project can wake up with its memories intact in the log
but out of reach of the context budget.

Every command therefore speaks to the automatically detected project in
`$PWD`. Hosted repositories use the complete remote identity; projects without
a usable remote use a portable local marker. `--global` reaches the one memory
that follows you everywhere. `wake` alone reads both: who you are, then where
you are. Almost everything belongs in the project; use `--global` only for
what would still be true tomorrow in a repository you have never seen.

## Files

```
~/.optmem/
  memo              the tool: one file of Python 3, no required dependencies
  memo.cmd          Windows launcher
  memory/           the global memory (create with `memo init`)
    LOG.txt         authoritative fixed-width records; append-only except redact
    TREE/           the summaries: a cache, rebuildable from the log alone
    QMD/            optional 16-memory Markdown projection, fully disposable
    config          the sizes, written by `memo config`
    scope.json      project stores only: canonical identity and safe aliases

$XDG_DATA_HOME/optmem/   (default: ~/.local/share/optmem)
  repo-v2/<name>-<hash>/ collision-resistant hosted-repository stores
  path-v2/<name>-<hash>/ local/path project stores
  repo/ and path/        compatible stores retained from older releases
  scope-map.json         links stable local identities to compatible stores
```

New hosted-project directory names use a readable repository label plus a
SHA-256-derived identity suffix; `scope.json` retains the full human-readable
identity. Existing compatible stores continue in place. If an old
`owner/repo` store is already claimed by a different host or namespace, OptMem
selects an isolated new store instead of mixing the histories.

```sh
memo config                  # show the sizes
memo config WAKE_LINES=300   # how many lines wake prints (208 ≈ 16k tokens)
memo config WAKE_LINES=      # back to the default
```

`WAKE_LINES` is usually the only size worth touching, and it is a reading
budget, not a storage budget: change it whenever, in either direction, and
nothing is recomputed. The same is true of `RAW_MAX`, the merge granularity —
how many raw memories one compression prompt shows before a block merges from
its two half summaries instead (default 16).

Records are fixed width, so position *is* identity and every lookup is one
seek. At a million memories (608 MB), `wake` takes 0.03s.

The widths themselves are per-store sizes too, which is how a store can hold
entries longer than 280 bytes — but they are *physical*, so they may only be
set while the store is empty:

```sh
memo config LOG_REC=640 TREE_REC=576 ENTRY_CHARS=560   # on a fresh store
```

`ENTRY_CHARS` is always capped at `min(TREE_REC - 8, LOG_REC - 40)` so every
entry and summary fits its record.

To widen an **existing** memory, migrate it — the one workflow where moving a
store directory by hand is sanctioned, because the moved directory doubles as
the migration backup:

```sh
memo export wider-backup.txt   # portable history; IDs are implied by order
memo scope                     # note the Store path (memo doctor for global)
mv <store> <store>.pre-widen   # set the old store aside, keep it as backup
memo config LOG_REC=640 TREE_REC=576 ENTRY_CHARS=560
memo import wider-backup.txt
memo nap --batch 8             # rebuild the summaries, a few jobs at a time
```

A project store reappears empty on first use, so `config` can size it
directly; for the global store (or a `MEMORY_DIR` store) run `memo init`
once before `config`. Amendment and retraction references survive because
import recreates the same IDs in order. Delete `<store>.pre-widen` only
after `memo doctor --deep` reports the new store healthy.

Set `$MEMORY_DIR` to pin a single store and skip scoping — a synced folder, a
git repo. See [WINDOWS.md](WINDOWS.md) for native PowerShell usage and locking
details.

`LOG.txt` is the source of truth. Raw records are immutable unless the user
explicitly invokes `redact --force`. `TREE/` is a rebuildable summary cache.
Backing up the memory directories is sufficient; never hand-edit them while an
agent may be writing.

## Upgrade, uninstall, and troubleshoot

Upgrade from any directory:

```sh
memo upgrade
```

This downloads the current installer over HTTPS. The installer validates the
new CLI before replacing the installed copy, refreshes PATH and completion
setup idempotently, and preserves logs, summaries, and configuration.

Remove OptMem's executable, PATH/profile hooks, and shell completion:

```sh
memo uninstall
```

Uninstall deliberately preserves the global store at `~/.optmem/memory` and
all project stores under `${XDG_DATA_HOME:-~/.local/share}/optmem`, so
reinstalling restores access to the same history. Open a new shell afterward
to discard the old process PATH. It also leaves managed blocks in connected
`AGENTS.md`, `CLAUDE.md`, and other instruction files; remove those blocks
manually if you are permanently retiring OptMem. Delete memory directories
manually only when you intentionally want to erase that data.

If QMD was enabled, run `memo qmd disable --purge` in each enabled scope
before uninstalling when you also want its external collection and local
projection removed.

Start troubleshooting with:

```sh
memo scope
memo doctor
memo --help
```

Common fixes:

- `memo: command not found`: open a new shell after installing, or re-run the
  installer to repair the PATH entry.
- The wrong project memory appears: run `memo scope` and check the current
  directory, selected remote, and detection source. In a checkout with several
  remotes and no suitable `origin` or tracked branch, set
  `OPTMEM_REMOTE=<remote-name>`.
- No global memory exists: run `memo init`.
- Fuzzy recall is unavailable: exact regex recall still works; install
  `fff-search` under Python 3.10+ to enable it.
- A command names `MEMORY_DIR`: verify that the environment variable points to
  the intended existing store.

Developing or contributing? See [CONTRIBUTING.md](CONTRIBUTING.md).

## Agent instruction block (reference)

`memo setup` writes the authoritative block between managed markers, using a
fully qualified executable path. This reference omits those markers and uses
the shorter PATH-based command for readability. Run `memo setup` (or copy the
version printed by `memo init`) instead of hand-editing this example.

```markdown
## Memory

Your memory is OptMem:
- The tool is `memo`
- Each automatically detected project has its own memory
- One global memory, `~/.optmem/memory`, follows you into all of them

OptMem outlives every session, compaction, model and vendor change.
Without it you do not know who you are, or what was decided and tried.

### Mental model

Each scope is an append-first log. `note` adds one raw memory with a stable
`#ID`; raw memories remain the source of truth. Later memories may cite earlier
`#IDs` when that makes a durable fact or decision unambiguous. `amend` appends a
corrected replacement; `retract` appends that an earlier memory is no longer
authoritative. The earlier record remains useful history. Only explicit,
user-directed `redact --force` rewrites a raw payload, to erase sensitive text.
Adjacent memories are also represented by a binary tree of lossy one-line
summaries. `wake` shows a bounded frontier from that tree—not full history—
with coarser summaries for older history and finer detail toward the present.
`recall` searches the raw log; `zoom` expands a summary toward its raw entries.

### At startup: activating OptMem (mandatory)

Run `memo wake` before any other tool call, in every session, and
then do exactly what it prints, through the end of its output. Read every
continuation until it says `You are awake.` and run any compression command it
prints before your next action. Without a `MEMORY_DIR` override, `wake` reads
the global memory first, then the automatically selected project.
If the selected project is ever unclear, `memo scope` reports the identity,
store, and detection source without changing memory.

### While working: register memories (mandatory)

Call `memo note "<1 line, max 280 UTF-8 bytes>"` whenever you learn
something durable enough to change future work. That covers the outcome
of a task worth real effort, a decision or constraint, a fact or insight
the user teaches you, or a preference they would reasonably expect retained.

That writes to the automatically selected project memory by default, which is
where almost everything belongs. Add `--global` ONLY if the memory would still
be true tomorrow in a repository you have never seen: who the user is, how they
want to be worked with, this machine, your own tooling. How one project does
something is not global, however much it feels like a lesson -- write it to
that project. A `MEMORY_DIR` override intentionally pins commands to one store.

If a line is over the byte limit, `--fit` on `note`, `amend`, or `retract`
trims it at a word boundary and reports exactly what was cut; rewrite only
if the cut loses something essential.

Do not register redundant memories. Never record secrets, credentials,
authentication material, or raw sensitive data.

Every memory you write is stamped with this session's opaque `@tag`.
Entries bearing another tag are a parallel session's testimony: weigh them
as reports, not as your own observations, and never restate them as yours.
Set OPTMEM_SESSION to name the tag; otherwise one is derived automatically.

If a durable memory changes, do not contradict it with an unexplained note.
Use `memo amend <id> "<replacement>"`; use
`memo retract <id> "<reason>"` when it has no replacement. When the
authoritative statement already lives in a compressed summary line `#a-b`,
amend the whole block: `memo amend <a>-<b> "<replacement>"` supersedes the
summarized range and stays linked to every raw memory inside it. Ordinary
memories may reference earlier `#IDs` to anchor stable facts and reasoning.
Use `memo show <id>` when you need the exact record and its later
references, including block supersessions that cover it;
`memo show <a>-<b>` shows a summary block and what supersedes it.
Redaction is not correction: only the user may request it, and it exists for
content that must actually be erased.

If `memo note` asks a compression, follow its prompt and run the exact
`nap` command before your next action. If `nap <range>` reports the wrong
block, a parallel session settled it first: run bare `memo nap` to get
the current job, and treat entries you did not write as another session's
testimony.
A compression is a lossy retrieval cue for the supplied range, not a
deletion: the raw memories remain searchable. Write one self-contained line.
Preserve durable decisions, outcomes, constraints, causal links, preferences,
and useful failure reasons. Drop transient status, incidental chronology, and
repetition. Use specific names; invent nothing and never imply a link between
unrelated facts. Later amendments, corrections, and retractions override the
records they reference. Preserve the final outcome; retain the earlier account
only when its history or failure reason remains useful.

Never edit or delete a memory directory: the tool manages it.

### When you need an old memory: search, or navigate

`memo recall <regex>` searches the complete raw log with a case-insensitive
regular expression and, when `fff-search` is installed, retries a zero-result
search fuzzily. Use
`memo recall --fuzzy "<text>"` to request typo-tolerant FFF recall
directly. Add `--limit N` to cap returned matches and `--context N` to
include neighboring raw memories; these control output without reducing the
history searched. Recall and `zoom` target project memory by default; put
`--global` before the command for global memory.
If QMD was explicitly enabled for this scope, use
`memo recall --semantic "<meaning>"` for meaning-based raw-memory recall;
add `--fast` to skip reranking for repeated related searches. QMD can also be
configured as the last fallback after exact and fuzzy recall both miss.

A `#a-b` line from `wake` is one summary node covering raw memory IDs
`a` through `b`. `memo zoom <a-b>` opens one level; add `--depth N`
to open up to six levels in one bounded call. Repeat until the relevant
raw memories appear.

### If you're a subagent: skip everything above

Parallel sessions on this machine are all you, and may all write memories.
A subagent is not: it must never run `memo`, because it cannot judge what
is already known, and its notes would arrive duplicated and incorrectly.
When you spawn one, write: `You are a subagent. Don't run memo.`
The parent agent remains responsible for recording the durable outcome.
```
