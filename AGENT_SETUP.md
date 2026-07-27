# Using OptMem with an AI agent

OptMem works by giving the agent a small, persistent instruction block. The
block tells the agent when to read memory, what to save, and how to retrieve
older information. You do not need an agent plugin or hosted service.

## 1. Connect the instructions

From the project the agent will work in, let OptMem add its managed CLI-use
instructions to any existing common instruction files:

```sh
cd /path/to/project
memo setup
```

On Windows PowerShell:

```powershell
Set-Location C:\path\to\project
memo setup
```

The managed block teaches the agent when to wake memory, what to save, how
project and global scopes differ, and how to recall and compress memories.
By default, `setup` only updates an existing `AGENTS.md` or `CLAUDE.md` and
reports each missing file it skipped. Run `memo setup --create` to explicitly
permit either missing file to be created. Use `--no-create` when a script
should state the safe default explicitly.

Existing files keep all of their content, with the OptMem block added at the
top. Re-running the command changes nothing when the block is current and
updates only the managed block when OptMem's instructions change.

Pass one or more paths to target different files:

```sh
memo setup AGENTS.md .github/agent-instructions.md
```

Add `--create` if any explicit target does not exist yet.

`setup` checks every requested file before writing any of them. It refuses
malformed managed markers, non-UTF-8 files, directories, and symbolic links
instead of guessing or partially configuring the project.

Setup only manages the instruction files. It does not create a project memory
or permanently bind the directory to one. OptMem detects scope whenever the
agent later runs a command.

For manual setup, run `memo init` and copy everything between:

```text
----- BEGIN OPTMEM AGENT INSTRUCTIONS -----
...
----- END OPTMEM AGENT INSTRUCTIONS -----
```

Paste it at the top of the persistent instruction file your agent reads for
every session. Common filenames are `AGENTS.md` and `CLAUDE.md`; other agents
may call this project instructions, rules, or system context. The HTML
`OPTMEM:START` and `OPTMEM:END` comments inside the generated block let a
future `memo setup` update it safely.

Use the generated block rather than copying the example from the README. It
contains the executable path that works on your platform.

## 2. Verify the installation

From the project where the agent will work:

```sh
memo scope
memo doctor
```

Check that:

- `scope` reports the expected `Project`, `Store`, and `Source`;
- `Active scope` says `project`;
- `Git remote` identifies the expected repository when one is selected;
- `Global store` exists;
- `Project store` points where you expect;
- FFF is either available or clearly marked optional.

Both commands are read-only. They do not create stores, identity markers, or
memories. Hosted projects use their full remote identity. Without a usable
remote, the first memory use creates a stable marker inside `.git` or, outside
Git, `.optmem-scope`, so moving the project does not change its memory.

## 3. Start the first session

Start a new agent session inside the repository. Before doing project work, the
agent should run `memo wake`.

Several outputs are possible:

- `You are awake.` — memory loading is complete.
- `Not awake yet. Run: ...` — the output was paginated; the agent must run the
  exact continuation command.
- `Cannot wake ... Run: ... nap ...` — a needed summary is pending; the agent
  must complete that compression and run wake again.
- `No global memory yet` — run `memo init`, then retry.

The agent should follow printed commands exactly until the output says it is
awake.

## What the agent should remember

Record information that will change a future decision or prevent repeated
work:

- architecture and invariants;
- durable user requirements and preferences;
- commands that are difficult to rediscover;
- important decisions and their reasons;
- failed approaches and why they failed;
- environment facts that will still matter in a later session;
- the outcome of substantial work.

Do not record:

- temporary progress such as “currently editing file X”;
- guesses that have not been verified;
- secrets, credentials, authentication material, or raw sensitive data;
- raw command output that can be reproduced cheaply;
- a fact already represented by an existing memory.

Memories are one line and at most 280 UTF-8 bytes. A good note is specific and
self-contained:

```sh
memo note "The API client retries 429 responses with capped exponential backoff."
```

Every raw memory has a stable `#ID`. An agent may cite an earlier `#ID` in a
later note when that makes a durable fact, decision, or reason precise. When a
claim changes, preserve the chronology instead of silently contradicting it:

```sh
memo show 42
memo amend 42 "Only idempotent reads are retried."
memo retract 42 "Obsolete after the HTTP client rewrite."
```

`amend` appends a corrected replacement. `retract` appends that the earlier
memory is no longer authoritative. Both keep the original as useful history,
and later lifecycle records override it during compression. `redact` is not an
agent correction tool: it physically erases a payload and should only be run
when the user explicitly requests removal of sensitive text.

## Project or global?

Use project memory by default.

```sh
memo note "Tests require the fixture server on port 4317."
```

Use global memory only when the fact remains true in an unrelated repository:

```sh
memo --global note "The user prefers PowerShell examples on Windows."
```

If the statement contains a repository name, file path, module, endpoint, or
project-specific convention, it almost certainly belongs to the project.

## Retrieving older information

Exact recall accepts a case-insensitive regular expression:

```sh
memo recall "retry|backoff"
memo --global recall "PowerShell"
```

Recall always searches the full raw log. Control the returned material without
reducing search coverage:

```sh
memo recall --limit 5 "retry|backoff"
memo recall --context 2 "deployment failed"
```

`--limit` caps matched memories. `--context` includes neighboring raw entries,
deduplicates overlapping windows, and marks the actual matches with `>`.

With optional `fff-search` installed, a zero-result exact query retries
fuzzily. Force typo-tolerant recall with:

```sh
memo recall --fuzzy "retrie bakoff"
```

If the user explicitly enabled QMD for this scope, meaning-based recall is
also available:

```sh
memo recall --semantic "Why did we stop retrying mutation requests?"
```

QMD selects relevant 16-memory projection segments. OptMem then resolves the
suggested IDs through the authoritative raw log, so treat the returned `#ID`
lines exactly like ordinary recall. A QMD failure does not mean the memory is
absent; retry with exact or FFF recall, or inspect `memo qmd status`.

Wake output may contain a summary such as `#128-255`. Open it without searching
the full log:

```sh
memo zoom --depth 3 128-255
```

Depth defaults to one and is capped at six. Repeat `zoom` on the relevant node
until the raw memories appear.

## Compression and large restores

Compression is a lossy retrieval cue; it never replaces the authoritative raw
records. Later amendments, corrections, and retractions override the records
they reference. Preserve the final outcome and keep an earlier account only
when its history or failure reason remains useful.

For a large import or summary rebuild, reduce agent/tool round trips:

```sh
memo nap --batch 8
```

Summarize each printed range independently, write the requested
`<range><TAB><summary>` lines to a UTF-8 file, then run:

```sh
memo nap --apply summaries.txt
```

OptMem validates the entire file before atomically adding any summaries.

## Parallel agents and subagents

Independent top-level sessions may safely write at the same time; OptMem
serializes writes with an advisory lock.

Subagents should not run OptMem. They lack the parent agent's full memory
context and tend to create duplicate or incorrectly scoped notes. Tell each
subagent:

```text
You are a subagent. Don't run memo.
```

The parent agent remains responsible for recording the durable outcome.

## Keeping the instructions current

After updating OptMem, run `memo setup` again in each connected project. It
updates only the content between OptMem's managed markers and does not change
existing memories or the rest of either instruction file.

If the instructions were copied before managed markers existed, `setup`
reports an unmanaged legacy block and leaves it alone. Remove that old block
once, then run `memo setup` to opt into managed updates.
