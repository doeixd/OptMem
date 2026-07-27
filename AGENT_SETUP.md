# Using OptMem with an AI agent

OptMem works by giving the agent a small, persistent instruction block. The
block tells the agent when to read memory, what to save, and how to retrieve
older information. You do not need an agent plugin or hosted service.

## 1. Generate the instructions

Run:

```sh
~/.optmem/memo init
```

On Windows PowerShell:

```powershell
& "$HOME\.optmem\memo.cmd" init
```

Copy everything between:

```text
----- BEGIN OPTMEM AGENT INSTRUCTIONS -----
...
----- END OPTMEM AGENT INSTRUCTIONS -----
```

Paste it at the top of the persistent instruction file your agent reads for
every session. Common filenames are `AGENTS.md` and `CLAUDE.md`; other agents
may call this project instructions, rules, or system context.

Use the generated block rather than copying the example from the README. It
contains the executable path that works on your platform.

## 2. Verify the installation

From the project where the agent will work:

```sh
~/.optmem/memo doctor
```

Check that:

- `Active scope` says `project`;
- `Git origin` identifies the expected repository, or explains the path
  fallback;
- `Global store` exists;
- `Project store` points where you expect;
- FFF is either available or clearly marked optional.

`doctor` is read-only. It does not create stores or write memories.

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
- secrets, tokens, passwords, or private keys;
- raw command output that can be reproduced cheaply;
- a fact already represented by an existing memory.

Memories are one line and at most 280 bytes. A good note is specific and
self-contained:

```sh
memo note "The API client retries 429 responses with capped exponential backoff."
```

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

With optional `fff-search` installed, a zero-result exact query retries
fuzzily. Force typo-tolerant recall with:

```sh
memo recall --fuzzy "retrie bakoff"
```

Wake output may contain a summary such as `#128-255`. Open it without searching
the full log:

```sh
memo zoom 128-255
```

Repeat `zoom` on the relevant half until the raw memories appear.

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

After updating OptMem, run `memo init` again. It does not change existing
memories; it prints the current instruction block. Replace the old block if
the generated text or executable path changed.
