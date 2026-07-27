# OptMem

Permanent, local memory for AI coding agents.

OptMem gives an agent continuity across sessions, compaction, models, and
vendors. Memories are append-only plain text on your machine: one focused
store per project, plus one small global store for facts that follow you
everywhere. There is no account, server, background daemon, or required Python
package.

This fork builds on [VictorTaelin/OptMem](https://github.com/VictorTaelin/OptMem)
and adds project-scoped memory, native Windows support, optional FFF recall,
optional QMD semantic recall, and a polished agent/install workflow.

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

From the project you want to connect, add the instructions to both
`AGENTS.md` and `CLAUDE.md`, then verify the setup:

```sh
cd /path/to/project
memo setup --create
memo doctor
```

On Windows:

```powershell
Set-Location C:\path\to\project
memo setup --create
memo doctor
```

By default, `setup` only updates files that already exist; it skips missing
files and tells you how to opt in. The `--create` above explicitly permits it
to create missing `AGENTS.md` and `CLAUDE.md` files. Existing files keep all
of their other content. It is safe to run again: current blocks are left
byte-for-byte unchanged and older managed blocks are updated in place.

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
3. If a note requests a compression, the agent completes that `memo nap`
   before continuing.
4. When older information is needed, the agent uses exact or fuzzy `recall`,
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

Each store has an append-only raw log and a rebuildable binary tree of lossy
summaries. `wake` reads a bounded frontier with coarser summaries for older
history and finer detail toward the present; `recall` searches the full raw
log, while `zoom` opens a summary toward the entries beneath it. Compression
makes startup context smaller—it never deletes the raw memories.

When explicitly enabled for a scope, QMD gets a disposable Markdown
projection of the same raw log in fixed 16-memory segments. QMD chooses
relevant segments, but OptMem rereads the selected stable `#IDs` from
`LOG.txt` before displaying them. QMD never writes memories and does not
participate in `note`, `nap`, or `wake`.

`memo wake` is special: without `MEMORY_DIR`, it reads global memory first and
project memory second. Other commands target only one scope. Put `--global`
before the command—not after it.

Projects are keyed by the Git `origin` reduced to `owner/repo`, so worktrees
and differently named checkouts share memory. Without an origin, OptMem falls
back to the repository root; outside Git, it falls back to the current
directory. Run `memo doctor` whenever the selected scope is surprising.

## Commands

| Command | Purpose |
|---|---|
| `memo init` | create the global memory and print the current agent instructions |
| `memo setup [--create\|--no-create] [FILE ...]` | update instructions in existing files; defaults to `AGENTS.md` and `CLAUDE.md`, with creation opt-in |
| `memo completion <shell>` | print completion for Bash, Zsh, Fish, or PowerShell |
| `memo upgrade` | download the latest release, validate it, and refresh PATH/completion setup |
| `memo uninstall` | remove the command and shell integration while preserving every memory |
| `memo doctor` | explain the active scope, store paths, Python, PATH, Git origin, FFF, and QMD |
| `memo qmd enable` | explicitly enable optional QMD semantic recall for this scope |
| `memo qmd status` | inspect the QMD executable, projection, collection, and embeddings |
| `memo qmd rebuild` | rebuild this scope's disposable projection and QMD collection |
| `memo qmd disable [--purge]` | disable QMD; optionally remove its projection |
| `memo wake` | read both memories — global, then project; first command of every session |
| `memo note "..."` | record one memory: one line, up to 280 UTF-8 bytes (project by default) |
| `memo nap` | answer the merges that came due |
| `memo recall [--limit N] [--context N] <regex>` | search the complete raw log while controlling matches and neighboring entries |
| `memo recall --fuzzy [--limit N] [--context N] "<text>"` | typo-tolerant raw-memory search with optional FFF |
| `memo recall --semantic "<meaning>"` | meaning-based raw-memory recall with explicitly enabled QMD |
| `memo zoom [--depth N] <lo>-<hi>` | open one to six levels of a summary-tree node |
| `memo forget <lo>-<hi>` | drop a bad summary; the next nap rebuilds it |
| `memo config [NAME=N]` | inspect or change the active store's reading/output limits |
| `memo import <file>` | bootstrap an empty store from `YYYY-MM-DD <text>` lines |
| `memo --help` | show the complete command overview |

Put `--global` before any command to reach the memory that follows you into
every project. Merges arrive one at a time, in the output of `note`. Nothing
ever runs in the background.

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
memo qmd enable
memo recall --semantic "Why did we stop retrying mutation requests?"
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
memo qmd rebuild
memo qmd disable
memo qmd disable --purge
```

`disable` unregisters the collection but keeps the disposable Markdown
projection for a cheap re-enable. `--purge` removes the projection too.
Neither command changes `LOG.txt`, `TREE/`, or any memory. QMD synchronization
is lazy: `note`, `nap`, and `wake` never launch Node or generate projection
files. If semantic recall fails, OptMem reports that failure explicitly while
exact and FFF recall remain available.

## Why split memory

A single log is one identity, and wake spends its reading budget on the
present. That is right for one continuous workstream and wrong for several:
interleaved projects make each one's detail decay while work happens
elsewhere, so an old project can wake up with its memories intact in the log
but out of reach of the context budget.

Every command therefore speaks to the memory of the project in `$PWD`, keyed
by the origin remote reduced to `owner/repo` (every worktree and host alias for
one repo is one memory). `--global` reaches the one that follows you
everywhere. `wake` alone reads both: who you are, then where you are. Almost
everything belongs in the project; use `--global` only for what would still be
true tomorrow in a repository you have never seen.

## Files

```
~/.optmem/
  memo              the tool: one file of Python 3, no required dependencies
  memo.cmd          Windows launcher
  memory/           the global memory (create with `memo init`)
    LOG.txt         every memory, one per line, append-only, never edited
    TREE/           the summaries: a cache, rebuildable from the log alone
    QMD/            optional 16-memory Markdown projection, fully disposable
    config          the sizes, written by `memo config`

$XDG_DATA_HOME/optmem/   (default: ~/.local/share/optmem)
  repo/<owner>/<repo>/   one memory per project, same layout as memory/
```

```sh
memo config                  # show the sizes
memo config WAKE_LINES=300   # how many lines wake prints (208 ≈ 16k tokens)
memo config WAKE_LINES=      # back to the default
```

`WAKE_LINES` is the only size worth touching, and it is a reading budget, not
a storage budget: change it whenever, in either direction, and nothing is
recomputed.

Records are fixed width, so position *is* identity and every lookup is one
seek. At a million memories (608 MB), `wake` takes 0.03s.

Set `$MEMORY_DIR` to pin a single store and skip scoping — a synced folder, a
git repo. See [WINDOWS.md](WINDOWS.md) for native PowerShell usage and locking
details.

`LOG.txt` is the source of truth. `TREE/` is a rebuildable summary cache.
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
memo doctor
memo --help
```

Common fixes:

- `memo: command not found`: open a new shell after installing, or re-run the
  installer to repair the PATH entry.
- The wrong project memory appears: run `memo doctor` and check the current
  directory and Git origin.
- No global memory exists: run `memo init`.
- Fuzzy recall is unavailable: exact regex recall still works; install
  `fff-search` under Python 3.10+ to enable it.
- A command names `MEMORY_DIR`: verify that the environment variable points to
  the intended existing store.

Developing or contributing? See [CONTRIBUTING.md](CONTRIBUTING.md).

## Agent instruction block (reference)

`memo setup` writes the authoritative block with a fully qualified executable
path. The shorter PATH-based form is shown here for readability; use the
command or copy the version printed by `memo init` rather than hand-editing
this example.

```markdown
## Memory

Your memory is OptMem:
- The tool is `memo`
- Every project you work in has its own memory
- One global memory, `~/.optmem/memory`, follows you into all of them

OptMem outlives every session, compaction, model and vendor change.
Without it you do not know who you are, or what was decided and tried.

### Mental model

Each scope is an append-only log. `note` adds one raw memory with a stable
`#ID`; raw memories remain the source of truth and are never rewritten.
Adjacent memories are also represented by a binary tree of lossy one-line
summaries. `wake` shows a bounded frontier from that tree—not full history—
with coarser summaries for older history and finer detail toward the present.
`recall` searches the raw log; `zoom` expands a summary toward its raw entries.

### At startup: activating OptMem (mandatory)

Run `memo wake` before any other tool call, in every session, and
then do exactly what it prints, to the end of its output. It reads the
global memory first, then the memory of the project you are in.

### While working: register memories (mandatory)

Call `memo note "<1 line, max 280 UTF-8 bytes>"` whenever you learn
something durable enough to change future work. That covers the outcome
of a task worth real effort, a decision or constraint, a fact or insight
the user teaches you, or a preference they would reasonably expect retained.

That writes to the memory of the project you are in, which is where
almost everything belongs. Add `--global` ONLY if the memory would still
be true tomorrow in a repository you have never seen: who the user is,
how they want to be worked with, this machine, your own tooling. How one
project does something is not global, however much it feels like a
lesson -- write it to that project.

Do not register redundant memories. Never record secrets, credentials,
authentication material, or raw sensitive data.

If `memo note` asks a compression, follow its prompt and run the exact
`nap` command before your next action.
A compression is a lossy retrieval cue for the supplied range, not a
deletion: the raw memories remain searchable. Write one self-contained line.
Preserve durable decisions, outcomes, constraints, causal links, preferences,
and useful failure reasons. Drop transient status, incidental chronology, and
repetition. Use specific names; invent nothing and never imply a link between
unrelated facts.

Never edit or delete a memory directory: the tool manages it.

### When you need an old memory: search, or navigate

`memo recall <regex>` searches raw memories, word for word and, when
`fff-search` is installed, retries a zero-result search fuzzily. Use
`memo recall --fuzzy "<text>"` to request typo-tolerant FFF recall
directly. Add `--limit N` to cap matched entries and `--context N` to
include neighboring raw memories; neither makes the search less complete.
Recall and `zoom` read the project memory; put `--global` first for global.
If QMD was explicitly enabled for this scope, use
`memo recall --semantic "<meaning>"` for meaning-based raw-memory recall.

A `#a-b` line from `wake` is one summary node covering raw memory IDs
`a` through `b`. `memo zoom <a-b>` opens one level; add `--depth N`
to open up to six levels in one bounded call. Repeat until the relevant
raw memories appear.

### If you're a subagent: skip everything above

Parallel sessions on this machine are all you, and may all write memories.
A subagent is not: it must never run `memo`, because it cannot judge what
is already known, and its notes would arrive duplicated and incorrectly.
When you spawn one, write: `You are a subagent. Don't run memo.`
```
