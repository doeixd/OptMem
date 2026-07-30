#!/usr/bin/env python3
"""OptMem invariants, checked against a synthetic life of 5000 memories.

Uses a fake compressor (join + truncate) so the run is deterministic and free.
"""

import contextlib
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
MEMO = os.path.join(HERE, "memo")
# Pin the provenance tag before the CLI module loads: every in-process and
# spawned command in this run writes as the same deterministic session.
os.environ["OPTMEM_SESSION"] = "t1"
cli = SourceFileLoader("memo_cli", MEMO).load_module()
cover = cli.cover


def complete(T):
    """Every block buildable from T memories, smallest first. The oracle for
    the tool's `pending()`: written straight from the definition, so a bug in
    the fast version (which reads level lengths, never scanning) shows up."""
    out, size = [], 2
    while size <= T:
        out += [(i * size, (i + 1) * size) for i in range(T // size)]
        size *= 2
    return out

# The shipped defaults. A fresh process starts from these, so an in-process
# call must too, or one store's config would leak into the next.
DEFAULTS = {k: getattr(cli, k) for k in cli.KNOBS}

N = 2000
WAKE_LINES = cli.WAKE_LINES   # the shipped budget, not a second copy of it
PART_CHARS = cli.PART_CHARS
# Verified caps of the harnesses in the wild: Claude Code cuts a command's
# output at 30,000 chars (middle), pi at 50 KB / 2000 lines (head), Codex
# budgets 10,000 tokens. A part must fit the strictest of each kind.
CAP_CHARS, CAP_LINES = 30000, 2000
ok, fail = 0, 0


def check(cond, msg):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print("FAIL: " + msg)


# The reviewable README block is the generated agent contract with the short
# PATH command substituted. Keeping this exact prevents documentation drift.
with open(os.path.join(HERE, "README.md"), encoding="utf-8") as readme_file:
    readme = readme_file.read()
documented = re.search(
    r"## Agent instruction block \(reference\).*?```markdown\n(.*?)\n```",
    readme, re.DOTALL)
expected_instructions = cli.TEMPLATE.format(
    memo="memo", data="~/.optmem/memory",
    chars=cli.KNOBS["ENTRY_CHARS"][0]).rstrip()
check(documented and documented.group(1).rstrip() == expected_instructions,
      "README agent instructions differ from the generated template")


# ---- pure block math -------------------------------------------------

for T in list(range(1, 400)) + [1000, 4096, 10000, 65536, 100003]:
    c = cover(T, WAKE_LINES)
    check(len(c) <= WAKE_LINES, "T=%d: %d lines > budget" % (T, len(c)))
    check(c[0][0] == 0 and c[-1][1] == T, "T=%d: does not span [0,T)" % T)
    for a, b in zip(c, c[1:]):
        check(a[1] == b[0], "T=%d: gap or overlap at %s %s" % (T, a, b))
    for lo, hi in c:
        s = hi - lo
        check(s & (s - 1) == 0 and lo % s == 0,
              "T=%d: [%d,%d) is not an aligned power-of-two block" % (T, lo, hi))
    for a, b in zip(c, c[1:]):
        check(b[1] - b[0] <= a[1] - a[0],
              "T=%d: detail does not increase toward the present" % T)

check(cover(300, 320) == [(i, i + 1) for i in range(300)],
      "under budget, memory should be verbatim")

# every block a cover ever needs must be buildable. cover() costs a 60-step
# binary search, so this walks every tree shape up to 300 and then samples:
# the property is structural, not a function of the exact T.
seen = set()
for T in list(range(1, 300)) + [512, 700, 1000, 1023, 1024, 2000, 2999]:
    seen.update(b for b in cover(T, WAKE_LINES) if b[1] - b[0] > 1)
buildable = set(complete(3000))
check(seen <= buildable, "a cover wants a block that complete() never yields")

# work never spikes: naps created by one new memory
worst, prev = 0, 0
for T in range(1, N):
    cur = len(complete(T))
    worst = max(worst, cur - prev)
    prev = cur
check(worst <= 16, "a single memory created %d naps" % worst)

# ---- the real CLI ----------------------------------------------------

d = tempfile.mkdtemp(prefix="optmem-test-")
memo = [sys.executable, MEMO]


class Result:
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def run(*args, store=None):
    """One `memo` command, in-process. Spawning an interpreter per call cost
    ~40ms x ~2000 naps; the cross-process behaviour that genuinely needs real
    processes (the lock) is tested with real processes below."""
    os.environ["MEMORY_DIR"] = store or d
    for k, v in DEFAULTS.items():
        setattr(cli, k, v)
    out, err, code = io.StringIO(), io.StringIO(), 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            sd = cli.store()
            cli.config(sd)
            cli.COMMANDS[args[0]](sd, list(args[1:]))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 0
    return Result(code, out.getvalue(), err.getvalue())


def nap_id(out):
    """The block id from the command a nap prompt offers."""
    m = re.search(r"\bnap (\d+)-(\d+)", out)
    return "%s-%s" % m.groups() if m else None


def offered(out):
    """The line offering a command. Every command handed to an agent must be
    an order, not a label: `Run: memo ...`, never `next: memo ...`."""
    return [l for l in out.splitlines()
            if l.startswith("Run: ") and (" nap " in l or " wake " in l)]


# the real entry point still has to work: shebang, argv parsing, exit code
smoke = subprocess.run(memo + ["wake"], env=dict(os.environ, MEMORY_DIR=d),
                       capture_output=True, text=True)
check(smoke.returncode == 0 and "No memories yet" in smoke.stdout,
      "the memo CLI does not run: " + smoke.stdout + smoke.stderr)

# a typo in MEMORY_DIR must not silently open a second, empty identity
ghost = subprocess.run(memo + ["wake"], capture_output=True, text=True,
                       env=dict(os.environ, MEMORY_DIR=d + "-typo"))
check(ghost.returncode == 1 and "No memory at" in ghost.stderr,
      "a missing MEMORY_DIR was created instead of reported")
check(not os.path.exists(d + "-typo"), "a missing MEMORY_DIR was created")
dry_source = d + "-dry-source.txt"
with open(dry_source, "w", encoding="utf-8") as f:
    f.write("2026-07-27 one portable record\n")
dry_missing = d + "-dry-store"
dry_cli = subprocess.run(
    memo + ["import", "--dry-run", dry_source],
    capture_output=True, text=True,
    env=dict(os.environ, MEMORY_DIR=dry_missing))
check(dry_cli.returncode == 0 and "Valid OptMem import" in dry_cli.stdout
      and not os.path.exists(dry_missing),
      "import --dry-run created a missing explicit store:\n"
      + dry_cli.stdout + dry_cli.stderr)
os.unlink(dry_source)

# the fresh-user path: no MEMORY_DIR, wake creates a project memory and says
# the global one is missing; init creates that one and remains idempotent
fresh = {k: v for k, v in os.environ.items() if k != "MEMORY_DIR"}
fresh["HOME"] = tempfile.mkdtemp()
fresh["USERPROFILE"] = fresh["HOME"]
fresh["XDG_DATA_HOME"] = os.path.join(fresh["HOME"], ".local", "share")
helped = subprocess.run(memo + ["--help"], capture_output=True, text=True,
                        env=fresh)
check(helped.returncode == 0 and "Usage:" in helped.stdout
      and "setup [--create|--no-create] [FILE ...]" in helped.stdout
      and "completion <shell>" in helped.stdout
      and "upgrade" in helped.stdout and "uninstall" in helped.stdout
      and "scope" in helped.stdout and "doctor" in helped.stdout
      and "qmd [help]" in helped.stdout
      and "recall [options]" in helped.stdout
      and "amend [--fit] <id|lo-hi>" in helped.stdout
      and "note [--fit]" in helped.stdout
      and "resummarize" in helped.stdout and "export [--with-ids]" in
          helped.stdout,
      "--help is not a useful command overview:\n" + helped.stdout + helped.stderr)
versioned = subprocess.run(memo + ["--version"], capture_output=True, text=True,
                           env=fresh)
version_command = subprocess.run(memo + ["version"], capture_output=True,
                                 text=True, env=fresh)
check(versioned.returncode == 0
      and versioned.stdout.strip() == "OptMem " + cli.VERSION
      and version_command.stdout == versioned.stdout,
      "version command/flag is missing or inconsistent")
help_alias = subprocess.run(memo + ["help"], capture_output=True, text=True,
                            env=fresh)
check(help_alias.returncode == 0 and help_alias.stdout == helped.stdout,
      "help and --help disagree")
completion_signatures = {
    "bash": "complete -F _memo_completion memo",
    "zsh": "compdef _memo memo",
    "fish": "complete -c memo",
    "powershell": "Register-ArgumentCompleter -Native -CommandName memo",
}
for shell, signature in completion_signatures.items():
    completed = subprocess.run(memo + ["completion", shell],
                               capture_output=True, text=True, env=fresh)
    check(completed.returncode == 0 and signature in completed.stdout,
          "%s completion is missing or invalid:\n%s%s"
          % (shell, completed.stdout, completed.stderr))
    check("upgrade" in completed.stdout and "uninstall" in completed.stdout
          and "scope" in completed.stdout,
          "%s completion omits maintenance commands" % shell)
    check("version" in completed.stdout,
          "%s completion omits the version command" % shell)
    check("limit" in completed.stdout and "context" in completed.stdout
          and "depth" in completed.stdout and "semantic" in completed.stdout
          and "fast" in completed.stdout and "qmd" in completed.stdout
          and "sync" in completed.stdout and "FALLBACK=on" in completed.stdout
          and "purge" in completed.stdout and "resummarize" in completed.stdout
          and "with-ids" in completed.stdout and "dry-run" in completed.stdout
          and "batch" in completed.stdout and "deep" in completed.stdout,
          "%s completion omits recall/zoom controls" % shell)
bad_completion = subprocess.run(memo + ["completion", "tcsh"],
                                capture_output=True, text=True, env=fresh)
check(bad_completion.returncode == 1
      and "<bash|zsh|fish|powershell>" in bad_completion.stderr,
      "an unsupported completion shell did not show valid choices")
check(not os.path.exists(os.path.join(fresh["HOME"], ".optmem", "memory")),
      "printing a completion script created a memory store")
for maintenance_command in ("upgrade", "uninstall"):
    refused = subprocess.run(memo + [maintenance_command],
                             capture_output=True, text=True, env=fresh)
    check(refused.returncode == 1 and "source checkout" in refused.stderr
          and ("memo %s" % maintenance_command) in refused.stderr,
          "%s did not protect the source checkout:\n%s%s"
          % (maintenance_command, refused.stdout, refused.stderr))
unknown = subprocess.run(memo + ["wat"], capture_output=True, text=True,
                         env=fresh)
check(unknown.returncode == 1 and "No such command: wat" in unknown.stderr
      and "Run:" in unknown.stderr and "--help" in unknown.stderr,
      "an unknown command did not point back to help:\n" + unknown.stderr)
diagnosed = subprocess.run(memo + ["doctor"], capture_output=True, text=True,
                           env=fresh)
check(diagnosed.returncode == 0 and "OptMem doctor" in diagnosed.stdout
      and "Active scope:" in diagnosed.stdout
      and "Global store:" in diagnosed.stdout and "FFF recall:" in diagnosed.stdout
      and "QMD integration:" in diagnosed.stdout,
      "doctor did not explain the installation:\n"
      + diagnosed.stdout + diagnosed.stderr)
check(not os.path.exists(os.path.join(fresh["HOME"], ".optmem", "memory")),
      "doctor created the missing global store")
scoped = subprocess.run(memo + ["scope"], capture_output=True, text=True,
                        env=fresh)
check(scoped.returncode == 0 and "OptMem scope" in scoped.stdout
      and "Project:" in scoped.stdout and "Store:" in scoped.stdout
      and "Source:" in scoped.stdout
      and not os.path.exists(os.path.join(fresh["XDG_DATA_HOME"], "optmem")),
      "scope did not explain selection read-only:\n"
      + scoped.stdout + scoped.stderr)
fresh_qmd_status = subprocess.run(
    memo + ["qmd", "status"], capture_output=True, text=True, env=fresh)
check(fresh_qmd_status.returncode == 0
      and "Integration:     disabled" in fresh_qmd_status.stdout
      and not os.path.exists(os.path.join(fresh["XDG_DATA_HOME"], "optmem")),
      "qmd status created a project store or failed read-only inspection:\n"
      + fresh_qmd_status.stdout + fresh_qmd_status.stderr)
fresh_qmd_help = subprocess.run(
    memo + ["qmd"], capture_output=True, text=True, env=fresh)
check(fresh_qmd_help.returncode == 0
      and "Optional QMD semantic recall" in fresh_qmd_help.stdout
      and "LOG.txt remains authoritative" in fresh_qmd_help.stdout
      and not os.path.exists(os.path.join(fresh["XDG_DATA_HOME"], "optmem")),
      "qmd help created a project store or omitted its mental model:\n"
      + fresh_qmd_help.stdout + fresh_qmd_help.stderr)
bad_qmd = subprocess.run(
    memo + ["qmd", "wat"], capture_output=True, text=True, env=fresh)
check(bad_qmd.returncode == 1 and "Run:" in bad_qmd.stderr
      and "qmd help" in bad_qmd.stderr
      and not os.path.exists(os.path.join(fresh["XDG_DATA_HOME"], "optmem")),
      "an invalid qmd command created a store or omitted help:\n"
      + bad_qmd.stdout + bad_qmd.stderr)
fresh_qmd_config = subprocess.run(
    memo + ["qmd", "config"], capture_output=True, text=True, env=fresh)
check(fresh_qmd_config.returncode == 0
      and "FALLBACK=off" in fresh_qmd_config.stdout
      and not os.path.exists(os.path.join(fresh["XDG_DATA_HOME"], "optmem")),
      "reading QMD fallback config created a project store:\n"
      + fresh_qmd_config.stdout + fresh_qmd_config.stderr)
fresh_qmd_fallback = subprocess.run(
    memo + ["qmd", "config", "FALLBACK=on"],
    capture_output=True, text=True, env=fresh)
check(fresh_qmd_fallback.returncode == 1
      and "requires an enabled integration" in fresh_qmd_fallback.stderr
      and not os.path.exists(os.path.join(fresh["XDG_DATA_HOME"], "optmem")),
      "enabling QMD fallback without QMD created a project store:\n"
      + fresh_qmd_fallback.stdout + fresh_qmd_fallback.stderr)
fresh_qmd_sync = subprocess.run(
    memo + ["qmd", "sync"], capture_output=True, text=True, env=fresh)
check(fresh_qmd_sync.returncode == 1
      and "not enabled" in fresh_qmd_sync.stderr
      and not os.path.exists(os.path.join(fresh["XDG_DATA_HOME"], "optmem")),
      "syncing disabled QMD created a project store:\n"
      + fresh_qmd_sync.stdout + fresh_qmd_sync.stderr)
fresh_semantic = subprocess.run(
    memo + ["recall", "--semantic", "anything"], capture_output=True,
    text=True, env=fresh)
check(fresh_semantic.returncode == 1 and "not enabled" in fresh_semantic.stderr
      and not os.path.exists(os.path.join(fresh["XDG_DATA_HOME"], "optmem")),
      "disabled semantic recall created a project store:\n"
      + fresh_semantic.stdout + fresh_semantic.stderr)

# `setup` safely connects common agent instruction files without requiring a
# memory to exist. It is repeatable, preserves user text, and preflights every
# target before writing any of them.
setup_dir = tempfile.mkdtemp(prefix="optmem-setup-")
setup = subprocess.run(memo + ["setup"], cwd=setup_dir,
                       capture_output=True, text=True, env=fresh)
agents = os.path.join(setup_dir, "AGENTS.md")
claude = os.path.join(setup_dir, "CLAUDE.md")
check(setup.returncode == 0 and not os.path.exists(agents)
      and not os.path.exists(claude)
      and setup.stdout.count("Skipped missing:") == 2
      and "--create" in setup.stdout,
      "setup created missing instruction files without opt-in:\n"
      + setup.stdout + setup.stderr)
no_create = subprocess.run(memo + ["setup", "--no-create"], cwd=setup_dir,
                           capture_output=True, text=True, env=fresh)
check(no_create.returncode == 0 and not os.path.exists(agents)
      and not os.path.exists(claude)
      and no_create.stdout.count("Skipped missing:") == 2,
      "setup --no-create did not enforce the safe default:\n"
      + no_create.stdout + no_create.stderr)
created = subprocess.run(memo + ["setup", "--create"], cwd=setup_dir,
                         capture_output=True, text=True, env=fresh)
check(created.returncode == 0 and os.path.isfile(agents)
      and os.path.isfile(claude) and created.stdout.count("Added OptMem") == 2,
      "setup --create did not create both default instruction files:\n"
      + created.stdout + created.stderr)
agents_before = open(agents, "rb").read()
claude_before = open(claude, "rb").read()
check(agents_before.count(cli.AGENT_START.encode()) == 1
      and b"Your memory is OptMem:" in agents_before,
      "AGENTS.md does not contain one managed instruction block")
setup_again = subprocess.run(memo + ["setup"], cwd=setup_dir,
                             capture_output=True, text=True, env=fresh)
check(setup_again.returncode == 0
      and setup_again.stdout.count("Already configured:") == 2
      and open(agents, "rb").read() == agents_before
      and open(claude, "rb").read() == claude_before,
      "setup is not byte-for-byte idempotent:\n"
      + setup_again.stdout + setup_again.stderr)
check(not os.path.exists(os.path.join(fresh["HOME"], ".optmem", "memory")),
      "setup created a memory store as a side effect")

custom = os.path.join(setup_dir, "CUSTOM.md")
with open(custom, "w", encoding="utf-8") as f:
    f.write("# Existing instructions\n\nKeep this exactly.\n")
custom_setup = subprocess.run(memo + ["setup", "CUSTOM.md"], cwd=setup_dir,
                              capture_output=True, text=True, env=fresh)
custom_text = open(custom, encoding="utf-8").read()
check(custom_setup.returncode == 0
      and custom_text.startswith(cli.AGENT_START)
      and custom_text.endswith("# Existing instructions\n\nKeep this exactly.\n"),
      "setup did not preserve existing custom instructions")

# A block managed by a previous release is updated in place; surrounding user
# content remains untouched.
with open(custom, "w", encoding="utf-8") as f:
    f.write("Before\n%s\nold generated instructions\n%s\nAfter\n"
            % (cli.AGENT_START, cli.AGENT_END))
updated = subprocess.run(memo + ["setup", custom], cwd=setup_dir,
                         capture_output=True, text=True, env=fresh)
custom_text = open(custom, encoding="utf-8").read()
check(updated.returncode == 0 and "Updated OptMem" in updated.stdout
      and custom_text.startswith("Before\n")
      and custom_text.endswith("\nAfter\n")
      and custom_text.count(cli.AGENT_START) == 1
      and "Your memory is OptMem:" in custom_text,
      "setup did not update one managed block in place")

legacy = os.path.join(setup_dir, "LEGACY.md")
legacy_text = "## Memory\n\nYour memory is OptMem:\n- hand maintained\n"
with open(legacy, "w", encoding="utf-8") as f:
    f.write(legacy_text)
legacy_setup = subprocess.run(memo + ["setup", legacy], cwd=setup_dir,
                              capture_output=True, text=True, env=fresh)
check(legacy_setup.returncode == 0 and "unmanaged legacy block" in legacy_setup.stdout
      and open(legacy, encoding="utf-8").read() == legacy_text,
      "setup duplicated or changed legacy hand-copied instructions")

malformed = os.path.join(setup_dir, "MALFORMED.md")
untouched = os.path.join(setup_dir, "MUST-NOT-EXIST.md")
with open(malformed, "w", encoding="utf-8") as f:
    f.write(cli.AGENT_START + "\nmissing end\n")
bad_setup = subprocess.run(memo + ["setup", "--create", untouched, malformed],
                           cwd=setup_dir, capture_output=True, text=True,
                           env=fresh)
check(bad_setup.returncode == 1 and "Malformed OptMem markers" in bad_setup.stderr
      and not os.path.exists(untouched),
      "setup wrote a partial result before rejecting malformed markers:\n"
      + bad_setup.stdout + bad_setup.stderr)
# AGENTS.md and CLAUDE.md are commonly ONE file under two names: a symlink
# either way, or a hard link where symlinks need a privilege. The block must
# land in it once, both names must still name it afterwards, and the link must
# survive -- setup must never replace a link with a regular file.
def linked_setup(kind):
    """Set up a fresh AGENTS.md/CLAUDE.md pair joined by one link, or None if
    this platform will not make that kind of link (Windows without the
    privilege). Returns (dir, target, link)."""
    where = tempfile.mkdtemp(prefix="optmem-linked-")
    target = os.path.join(where, "AGENTS.md")
    link = os.path.join(where, "CLAUDE.md")
    with open(target, "w", encoding="utf-8") as f:
        f.write("# House rules\n\nBe careful.\n")
    try:
        if kind == "symlink":
            os.symlink(target, link)
        else:
            os.link(target, link)
    except (OSError, NotImplementedError, AttributeError):
        shutil.rmtree(where, ignore_errors=True)
        return None
    return where, target, link


for kind in ("symlink", "hardlink"):
    made = linked_setup(kind)
    if made is None:
        continue           # the platform refused; the other kind still covers
    where, target, link = made
    for order in (["AGENTS.md", "CLAUDE.md"], ["CLAUDE.md", "AGENTS.md"], []):
        made_each = linked_setup(kind)
        if made_each is None:
            continue
        where_each, target_each, link_each = made_each
        r = subprocess.run(memo + ["setup"] + order, cwd=where_each,
                           capture_output=True, text=True, env=fresh)
        body = open(target_each, encoding="utf-8").read()
        check(r.returncode == 0,
              "%s setup %s failed:\n%s" % (kind, order, r.stdout + r.stderr))
        check(body.count(cli.AGENT_START) == 1
              and body.count(cli.AGENT_END) == 1,
              "%s setup %s wrote the block %d times into one file"
              % (kind, order, body.count(cli.AGENT_START)))
        check("Be careful." in body,
              "%s setup %s dropped the file's own instructions" % (kind, order))
        # both names must still be one file, with one content
        check(os.path.exists(link_each) and os.path.exists(target_each)
              and os.path.samefile(link_each, target_each),
              "%s setup %s broke the link between the two names"
              % (kind, order))
        check(open(link_each, encoding="utf-8").read() == body,
              "%s setup %s left the two names with different contents"
              % (kind, order))
        if kind == "symlink":
            check(os.path.islink(link_each) and not os.path.islink(target_each),
                  "%s setup %s replaced the symlink with a regular file"
                  % (kind, order))
        check(r.stdout.count("Added OptMem instructions") == 1,
              "%s setup %s did not report exactly one file written:\n%s"
              % (kind, order, r.stdout))
        check("same file" in r.stdout,
              "%s setup %s did not say the second name is the same file:\n%s"
              % (kind, order, r.stdout))
        # and it stays idempotent through the link
        again = subprocess.run(memo + ["setup"] + order, cwd=where_each,
                               capture_output=True, text=True, env=fresh)
        check(again.returncode == 0
              and again.stdout.count("Already configured:") == 1
              and open(target_each, encoding="utf-8").read() == body,
              "%s setup %s is not idempotent through the link:\n%s"
              % (kind, order, again.stdout + again.stderr))
        shutil.rmtree(where_each, ignore_errors=True)
    shutil.rmtree(where, ignore_errors=True)

# A symlink pointing nowhere must be reported, not silently turned into a
# regular file that the link's owner never asked for.
dangling_dir = tempfile.mkdtemp(prefix="optmem-dangling-")
dangling = os.path.join(dangling_dir, "AGENTS.md")
try:
    os.symlink(os.path.join(dangling_dir, "nowhere.md"), dangling)
except (OSError, NotImplementedError):
    dangling = None
if dangling:
    r = subprocess.run(memo + ["setup", "--create", "AGENTS.md"],
                       cwd=dangling_dir, capture_output=True, text=True,
                       env=fresh)
    check(r.returncode == 1 and "does not exist" in r.stderr
          and os.path.islink(dangling),
          "setup followed a dangling symlink instead of reporting it:\n"
          + r.stdout + r.stderr)
shutil.rmtree(dangling_dir, ignore_errors=True)

conflicting_setup = subprocess.run(
    memo + ["setup", "--create", "--no-create"], cwd=setup_dir,
    capture_output=True, text=True, env=fresh)
check(conflicting_setup.returncode == 1
      and "either --create or --no-create" in conflicting_setup.stderr,
      "setup accepted conflicting creation flags")

# Uninstall removes only the installed command and integration it owns. It
# must leave both global and project memories—and unrelated profile text—intact.
maintenance_home = tempfile.mkdtemp(prefix="optmem-maintenance-")
maintenance_install = os.path.join(maintenance_home, ".optmem")
os.makedirs(os.path.join(maintenance_install, "memory"))
installed_memo = os.path.join(maintenance_install, "memo")
shutil.copy2(MEMO, installed_memo)
installed_launcher = installed_memo + ".cmd"
with open(installed_launcher, "w", encoding="utf-8") as f:
    f.write("@echo off\n")
with open(os.path.join(maintenance_install, "memory", "KEEP"), "w") as f:
    f.write("global memory")
maintenance_xdg = os.path.join(maintenance_home, "data")
project_keep = os.path.join(maintenance_xdg, "optmem", "KEEP")
os.makedirs(os.path.dirname(project_keep))
with open(project_keep, "w") as f:
    f.write("project memory")
if os.name == "nt":
    completion_file = os.path.join(maintenance_install, "memo-completion.ps1")
    profile_file = os.path.join(
        maintenance_home, "Documents", "WindowsPowerShell", "profile.ps1")
    managed_line = ". '%s'" % completion_file.replace("'", "''")
else:
    completion_file = os.path.join(
        maintenance_xdg, "bash-completion", "completions", "memo")
    profile_file = os.path.join(maintenance_home, ".bashrc")
    managed_line = 'export PATH="$HOME/.optmem:$PATH"'
os.makedirs(os.path.dirname(completion_file), exist_ok=True)
with open(completion_file, "w") as f:
    f.write("completion")
os.makedirs(os.path.dirname(profile_file), exist_ok=True)
with open(profile_file, "w", encoding="utf-8") as f:
    f.write("before\n# OptMem %s\n%s\nafter\n"
            % ("completion" if os.name == "nt" else "command", managed_line))
maintenance_env = dict(
    fresh, HOME=maintenance_home, USERPROFILE=maintenance_home,
    XDG_DATA_HOME=maintenance_xdg)

# Exercise the full upgrade downloader/runner against a local authoritative
# installer, never the network or the developer's real installation.
check("releases/latest/download/memo" in open(
          os.path.join(HERE, "install.sh"), encoding="utf-8").read()
      and "releases/latest/download" in open(
          os.path.join(HERE, "install.ps1"), encoding="utf-8").read()
      and "releases/latest/download" in cli.INSTALL_BASE,
      "install or upgrade still consumes mutable main-branch payloads")
fake_release = tempfile.mkdtemp(prefix="optmem-release-")
upgrade_marker = os.path.join(fake_release, "UPGRADED")
if os.name == "nt":
    fake_installer = os.path.join(fake_release, "install.ps1")
    with open(fake_installer, "w", encoding="utf-8") as f:
        f.write("# OptMem installer\n"
                "[IO.File]::WriteAllText($env:OPTMEM_UPGRADE_MARKER, 'ok')\n")
else:
    fake_installer = os.path.join(fake_release, "install.sh")
    with open(fake_installer, "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\n# OptMem installer\n"
                "printf ok > \"$OPTMEM_UPGRADE_MARKER\"\n")
saved_module_file, saved_install_base = cli.__file__, cli.INSTALL_BASE
saved_maintenance_env = {
    key: os.environ.get(key) for key in
    ("HOME", "USERPROFILE", "XDG_DATA_HOME", "OPTMEM_UPGRADE_MARKER")}
upgrade_code = 0
try:
    os.environ.update(maintenance_env)
    os.environ["OPTMEM_UPGRADE_MARKER"] = upgrade_marker
    cli.__file__ = installed_memo
    from pathlib import Path
    cli.INSTALL_BASE = Path(fake_release).as_uri()
    try:
        cli.cmd_upgrade(None, [])
    except SystemExit as e:
        upgrade_code = e.code if isinstance(e.code, int) else 1
finally:
    cli.__file__, cli.INSTALL_BASE = saved_module_file, saved_install_base
    for key, value in saved_maintenance_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
check(upgrade_code == 0 and os.path.exists(upgrade_marker)
      and open(upgrade_marker).read() == "ok",
      "upgrade did not download and execute the validated installer")

removed = subprocess.run(
    [sys.executable, installed_memo, "uninstall"],
    capture_output=True, text=True, env=maintenance_env)
check(removed.returncode == 0 and "Preserved global memory" in removed.stdout
      and "Preserved project memories" in removed.stdout
      and "Agent instruction blocks were not removed" in removed.stdout
      and "QMD projections and external collections were not removed" in
          removed.stdout,
      "uninstall failed:\n" + removed.stdout + removed.stderr)
check(not os.path.exists(installed_memo)
      and not os.path.exists(installed_launcher)
      and not os.path.exists(completion_file),
      "uninstall left installed executable or completion files")
check(os.path.exists(os.path.join(maintenance_install, "memory", "KEEP"))
      and os.path.exists(project_keep),
      "uninstall deleted memory data")
profile_after = open(profile_file, encoding="utf-8").read()
check(profile_after == "before\nafter\n",
      "uninstall damaged unrelated profile content: %r" % profile_after)

noenv = subprocess.run(memo + ["wake"], capture_output=True, text=True, env=fresh)
check(noenv.returncode == 0 and "No global memory yet" in noenv.stdout
      and " init" in noenv.stdout,
      "with no global memory, wake must still work and point at init:\n"
      + noenv.stdout + noenv.stderr)
init = subprocess.run(memo + ["init"], capture_output=True, text=True, env=fresh)
check(init.returncode == 0 and "## Memory" in init.stdout
      and "You are a" in init.stdout, "init must print the AGENTS.md block")
check("append-first log" in init.stdout
      and "binary tree of lossy one-line" in init.stdout
      and "raw memories remain searchable" in init.stdout
      and "max 280 UTF-8 bytes" in init.stdout
      and "recall --semantic" in init.stdout
      and "amend <id>" in init.stdout and "retract <id>" in init.stdout
      and "Later amendments, corrections, and retractions override" in
          init.stdout
      and "may reference earlier `#IDs`" in init.stdout
      and "Never record secrets, credentials" in init.stdout,
      "agent instructions do not explain the memory/compression model")
check("BEGIN OPTMEM AGENT INSTRUCTIONS" in init.stdout
      and "END OPTMEM AGENT INSTRUCTIONS" in init.stdout
      and "Verify this installation" in init.stdout,
      "init did not delimit the copyable block or explain the next step")
check(os.path.exists(os.path.join(fresh["HOME"], ".optmem", "memory", "config")),
      "init must create ~/.optmem/memory with its config")
global_doctor = subprocess.run(memo + ["--global", "doctor"],
                               capture_output=True, text=True, env=fresh)
check(global_doctor.returncode == 0
      and "Active scope:  global" in global_doctor.stdout
      and "Active store:" in global_doctor.stdout,
      "--global doctor did not diagnose the global store:\n"
      + global_doctor.stdout + global_doctor.stderr)
if "On PATH:       no" in global_doctor.stdout:
    check("PATH is not active in this shell; open a new shell" in
          global_doctor.stdout,
          "doctor did not explain how to recover a missing PATH:\n"
          + global_doctor.stdout)
override_dir = os.path.join(fresh["HOME"], "missing-explicit-store")
override_doctor = subprocess.run(
    memo + ["doctor"], capture_output=True, text=True,
    env=dict(fresh, MEMORY_DIR=override_dir))
check(override_doctor.returncode == 0
      and "Active scope:  MEMORY_DIR override" in override_doctor.stdout
      and "not created" in override_doctor.stdout
      and not os.path.exists(override_dir),
      "doctor did not safely diagnose a missing MEMORY_DIR:\n"
      + override_doctor.stdout + override_doctor.stderr)
again = subprocess.run(memo + ["init"], capture_output=True, text=True, env=fresh)
check(again.returncode == 0 and "Found" in again.stdout, "init must be idempotent")
woke = subprocess.run(memo + ["wake"], capture_output=True, text=True, env=fresh)
check(woke.returncode == 0 and "You are awake." in woke.stdout,
      "after init, wake must work with zero configuration")

# Every command the tool prints must RUN on the machine it printed it on.
# `curl | sh` puts nothing on PATH, so a bare `memo nap ...` would not: the
# whole loop (note -> merge prompt -> nap) dies on `command not found`.
bare = dict(fresh, PATH=(os.environ.get("PATH", "") if os.name == "nt"
                         else "/usr/bin:/bin"))
subprocess.run(memo + ["note", "the first thing that happened"], env=bare,
               capture_output=True)
asked = subprocess.run(memo + ["note", "the second thing that happened"],
                       env=bare, capture_output=True, text=True)
order = [l[5:] for l in asked.stdout.splitlines() if l.startswith("Run: ")]
check(len(order) == 1, "note did not order a compression: " + asked.stdout)
command = order[0].replace('"<your line>"', '"both things"')
if os.name == "nt":
    obeyed = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                            env=bare, capture_output=True, text=True)
else:
    obeyed = subprocess.run(command, shell=True, env=bare,
                            capture_output=True, text=True)
check(obeyed.returncode == 0 and "saved" in obeyed.stdout,
      "the order the tool printed does not run with nothing on PATH: %r -> %s"
      % (order[0], obeyed.stderr.strip()))

# A paginated global wake must enter the project after its final page, and a
# paginated project must then continue without restarting global page 1.
subprocess.run(memo + ["--global", "config", "PART_LINES=1"],
               env=fresh, capture_output=True, check=True)
for text in ("global page one", "global page two"):
    subprocess.run(memo + ["--global", "note", text],
                   env=fresh, capture_output=True, check=True)
subprocess.run(memo + ["config", "PART_LINES=1"],
               env=fresh, capture_output=True, check=True)
for text in ("project page one", "project page two"):
    subprocess.run(memo + ["note", text],
                   env=fresh, capture_output=True, check=True)
page1 = subprocess.run(memo + ["wake"], env=fresh,
                       capture_output=True, text=True, check=True)
continuations = [l.split("Run: ", 1)[1] for l in page1.stdout.splitlines()
                 if "Run: " in l and "--then-project" in l]
check(len(continuations) == 1,
      "a paged global wake lost its project continuation:\n" + page1.stdout)
if continuations:
    if os.name == "nt":
        bridge = subprocess.run(
            ["powershell", "-NoProfile", "-Command", continuations[0]],
            env=fresh, capture_output=True, text=True)
    else:
        bridge = subprocess.run(continuations[0], shell=True, env=fresh,
                                capture_output=True, text=True)
    check(bridge.returncode == 0
          and "global page two" in bridge.stdout
          and "== Project memory:" in bridge.stdout,
          "global pagination never entered the project:\n"
          + bridge.stdout + bridge.stderr)
    project_pages = [bridge.stdout]
    current = bridge
    for _ in range(16):
        project_continuations = [
            l.split("Run: ", 1)[1] for l in current.stdout.splitlines()
            if l.startswith("Not awake yet. Run: ")
            and "--then-project" not in l
        ]
        if not project_continuations:
            break
        check(len(project_continuations) == 1,
              "project pagination did not print one project continuation:\n"
              + current.stdout)
        if len(project_continuations) != 1:
            break
        if os.name == "nt":
            current = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 project_continuations[0]],
                env=fresh, capture_output=True, text=True)
        else:
            current = subprocess.run(project_continuations[0], shell=True,
                                     env=fresh, capture_output=True, text=True)
        project_pages.append(current.stdout)
        check(current.returncode == 0
              and "== Global memory:" not in current.stdout
              and "global page one" not in current.stdout,
              "project continuation restarted global memory:\n"
              + current.stdout + current.stderr)
    project_document = "\n".join(project_pages)
    check("project page one" in project_document
          and "project page two" in project_document
          and project_document.count("You are awake.") == 1
          and "Not awake yet." not in current.stdout,
          "project pagination did not finish exactly once:\n"
          + project_document + current.stderr)

# a size written by hand into `config` must not brick the tool with a
# recovery that is itself broken: name the file and the line
badcfg = os.path.join(fresh["HOME"], ".optmem", "memory", "config")
with open(badcfg, "a") as f:
    f.write("WAKE_LNES = 100\n")
for c in (["wake"], ["--global", "config"]):
    r_ = subprocess.run(memo + c, capture_output=True, text=True, env=fresh)
    check(r_.returncode == 1 and "config line" in r_.stderr
          and "WAKE_LNES" in r_.stderr,
          "a typo in config does not say where it is: " + r_.stderr)
open(badcfg, "w").write("")

# the filesystem is the one thing the tool does not control: report it in the
# tool's own voice, never as a Python traceback
r_ = subprocess.run(memo + ["init"], capture_output=True, text=True,
                    env=dict(fresh, MEMORY_DIR=MEMO))   # a file, not a store
check(r_.returncode == 1 and "Traceback" not in r_.stderr
      and ("Not a directory" in r_.stderr
           or "cannot find the path specified" in r_.stderr.lower()),
      "a filesystem error printed a traceback: " + r_.stderr)


r = run("note", "x" * 281)
check(r.returncode == 1 and "Too long" in r.stderr, "over-long note accepted")
check("over by 1" in r.stderr and 'last 1 bytes: "x"' in r.stderr,
      "an over-long note does not show what to cut: " + r.stderr)
r = run("note", "x" * 280 + "é")   # 282 bytes: the 2-byte char must be shown
check(r.returncode == 1 and 'last 2 bytes: "é"' in r.stderr,
      "the overage suffix split a multi-byte character: " + r.stderr)
r = run("note", "two\nlines")
check(r.returncode == 1 and "one line" in r.stderr, "multi-line note accepted")
r = run("note", "   ")
check(r.returncode == 1, "empty note accepted")
r = run("wake")
check("No memories yet" in r.stdout, "empty wake should say so")
check(r.stdout.rstrip().endswith("You are awake."),
      "an empty wake must still end with `You are awake.`")

with open(os.path.join(d, "seed.txt"), "w") as f:
    day = datetime.date(2020, 1, 1)
    for i in range(N):
        f.write("%s memory number %d, a thing that happened\n"
                % ((day + datetime.timedelta(days=i // 5)).isoformat(), i))
r = run("import", os.path.join(d, "seed.txt"))
check("Imported %d" % N in r.stdout, "import failed: " + r.stdout + r.stderr)
check(not os.path.exists(os.path.join(d, "config")),
      "a store wrote its own config file: the defaults are now frozen in it")

r = run("wake")
check(r.returncode == 1 and "Cannot wake" in r.stdout,
      "wake must refuse while work is pending")
check("wake again" in r.stdout,
      "the refusal must order the agent back to wake")
check("None" not in r.stdout, "the refusal printed a Python None")

# nap loop, with a fake compressor
naps = 0
r = run("nap")
check("Compress memories #" in r.stdout, "nap prompt must name its object")
check("self-contained retrieval cue" in r.stdout
      and "causal links" in r.stdout
      and "UTF-8 bytes" in r.stdout
      and "never imply a link between unrelated facts" in r.stdout,
      "nap prompt does not give enough compression guidance")
while "Nothing left to compress" not in r.stdout:
    line = offered(r.stdout)
    check(bool(line), "no command offered:\n" + r.stdout + r.stderr)
    if not line:
        break
    check(line[0].startswith("Run: "), "a command was offered as a label, not "
          "an order: %r" % line[0])
    bid = nap_id(r.stdout)
    body = [l.strip() for l in r.stdout.splitlines() if l.startswith("  #")]
    r = run("nap", bid, (" ".join(body)[:280]).strip() or "empty")
    check(r.returncode == 0, "nap rejected a valid merge: " + r.stderr)
    naps += 1
check("You are awake" not in r.stdout,
      "nap must never claim the agent is awake; only wake may")
check(naps == len(complete(N)), "did %d naps, expected %d" % (naps, len(complete(N))))

r = run("wake")
check(r.returncode == 0, "wake still refuses after a full nap chain")

# the document survives pagination, and every part fits every harness's cap
parts, k = [], 1
while True:
    r = run("wake", str(k))
    if r.returncode != 0:
        break
    body = [l for l in r.stdout.splitlines() if l.startswith("#")]
    check(len(r.stdout) < CAP_CHARS, "part %d is %d chars, over the %d cap"
          % (k, len(r.stdout), CAP_CHARS))
    check(len(r.stdout.splitlines()) < CAP_LINES, "part %d is over %d lines"
          % (k, CAP_LINES))
    parts.append(body)
    k += 1
check(len(parts) > 1, "a %d-line memory should need more than one part" % WAKE_LINES)
lines = [l for p in parts for l in p]
check(len(lines) == WAKE_LINES, "woke with %d lines, want %d" % (len(lines), WAKE_LINES))
check(lines[-1].startswith("#%d " % (N - 1)), "newest memory not last / not raw")
check(lines[0].startswith("#0-"), "oldest line should be a summary block")
check(re.search(r"Run: .* wake 2", run("wake").stdout),
      "part 1 must ORDER the next command, not label it")
last_part = run("wake", str(len(parts))).stdout
check("You are awake." in last_part, "last part must say it is last")
# the final part reports what the bounded frontier elided, and how to expand
check(re.search(r"Frontier: \d+ of [\d,]+ memories shown raw; .*zoom \d+-\d+",
                last_part),
      "the last part does not report the elided frontier:\n" + last_part)
check(run("wake", str(len(parts) + 1)).returncode == 1, "a nonexistent part should fail")

# append-only: nothing was ever rewritten
logsz = os.path.getsize(os.path.join(d, "LOG.txt"))
run("note", "one more thing happened today")
check(os.path.getsize(os.path.join(d, "LOG.txt")) > logsz, "note did not append")
check(logsz % 320 == 0, "LOG.txt is not a whole number of records")
for f in os.listdir(os.path.join(d, "TREE")):
    check(os.path.getsize(os.path.join(d, "TREE", f)) % 288 == 0,
          "TREE/%s is not a whole number of records" % f)

# a nap when nothing is pending writes nothing and says so
r = run("nap", "0-1", "attempted overwrite")
check(r.returncode == 0 and "Nothing left to compress" in r.stdout,
      "nap with nothing pending must say so and write nothing")

# recall reaches memories the summaries lost, and matches the whole line:
# id and date included, not just the text
r = run("recall", "memory number 7,")
check(r.returncode == 0 and "#7 " in r.stdout, "recall missed a memory")
check("1 match." in r.stdout, "a single match is not `1 matches`: " + r.stdout)
r = run("recall", "^#7 ")
check("memory number 7," in r.stdout, "recall cannot find a memory by id")
# a query the shell split into words is rejoined, not rejected
r = run("recall", "memory", "number", "7,")
check(r.returncode == 0 and "#7 " in r.stdout,
      "recall did not rejoin a shell-split query: " + r.stdout + r.stderr)
r = run("recall", "2020-01-02")
check("#7 " in r.stdout and "5 matches." in r.stdout,
      "recall cannot find memories by date: " + r.stdout)

# Limit caps matching entries without reducing search coverage; context adds
# deduplicated neighboring raw memories and clearly marks the actual hits.
r = run("recall", "--limit", "3", "memory number")
limited = [line for line in r.stdout.splitlines() if line.startswith("#")]
check([int(line.split()[0][1:]) for line in limited] == [N - 3, N - 2, N - 1]
      and "--limit 3" in r.stdout,
      "recall --limit did not keep the newest three matches:\n" + r.stdout)
r = run("recall", "--context", "2", "^#7 ")
context_lines = [line for line in r.stdout.splitlines()
                 if re.match(r"[ >] #\d+ ", line)]
check(len(context_lines) == 5
      and context_lines[0].startswith("  #5 ")
      and context_lines[2].startswith("> #7 ")
      and context_lines[-1].startswith("  #9 ")
      and "Context: up to 2 memories" in r.stdout,
      "recall --context did not surround and mark one hit:\n" + r.stdout)
r = run("recall", "--context=2", r"^#(?:7|9) ")
context_lines = [line for line in r.stdout.splitlines()
                 if re.match(r"[ >] #\d+ ", line)]
check(len(context_lines) == 7
      and sum(line.startswith("> ") for line in context_lines) == 2,
      "overlapping recall context was not deduplicated:\n" + r.stdout)
check(run("recall", "--limit", "0", "x").returncode == 1,
      "recall accepted a zero result limit")
check(run("recall", "--context", "21", "x").returncode == 1,
      "recall accepted excessive context")

# FFF is optional: exact recall stays dependency-free, while explicit fuzzy
# recall and zero-result fallback consume its strongest-first raw-memory lines.
real_fff_recall = cli.fff_recall
fuzzy_line = "#7 2020-01-02 memory number 7, a thing that happened"
cli.fff_recall = lambda store, query, limit=None: ([fuzzy_line], None)
r = run("recall", "--fuzzy", "memry numbr sevn")
check(r.returncode == 0 and fuzzy_line in r.stdout
      and "FFF, strongest first" in r.stdout,
      "explicit FFF recall did not render its result: " + r.stdout + r.stderr)
r = run("recall", "--fuzzy", "--context", "1", "memry numbr sevn")
check("> #7 " in r.stdout and "  #6 " in r.stdout and "  #8 " in r.stdout
      and "context deduplicated" in r.stdout,
      "fuzzy recall context did not render neighboring raw memories:\n"
      + r.stdout)
r = run("recall", "definitely absent exact phrase")
check(r.returncode == 0 and "No exact match" in r.stdout
      and fuzzy_line in r.stdout,
      "zero exact results did not fall back to FFF: " + r.stdout + r.stderr)
cli.fff_recall = lambda store, query, limit=None: (None, "not installed")
r = run("recall", "--fuzzy", "anything")
check(r.returncode == 1 and "pip install fff-search" in r.stderr,
      "forced FFF recall did not explain how to enable it: " + r.stderr)
cli.fff_recall = real_fff_recall


# Lifecycle records are later events over stable IDs, not mutations of prior
# facts. Export/import preserves those IDs by restoring only into an empty log.
life = tempfile.mkdtemp(prefix="optmem-lifecycle-")
os.makedirs(os.path.join(life, "TREE"))
open(os.path.join(life, "LOG.txt"), "wb").close()
original = run("note", "Mutation requests may be retried.", store=life)
check("Saved as #0" in original.stdout, "lifecycle seed note failed")
amended = run("amend", "#0", "Only idempotent reads are retried.", store=life)
retracted = run("retract", "0", "Obsolete after the client rewrite.", store=life)
run("note", "The migration rationale is anchored by #0 and #1.", store=life)
check(amended.returncode == 0 and "Amended #0 with #1" in amended.stdout,
      "amend did not append a stable lifecycle event: "
      + amended.stdout + amended.stderr)
check(retracted.returncode == 0 and "Retracted #0 with #2" in retracted.stdout,
      "retract did not append a stable lifecycle event")
shown = run("show", "0", store=life)
check("Mutation requests may be retried." in shown.stdout
      and "Later references:" in shown.stdout
      and "Amends #0:" in shown.stdout and "Retracts #0:" in shown.stdout
      and "anchored by #0" in shown.stdout,
      "show did not resolve the canonical record and later references:\n"
      + shown.stdout + shown.stderr)
before_invalid = os.path.getsize(os.path.join(life, "LOG.txt"))
invalid_amend = run("amend", "99", "Impossible target.", store=life)
check(invalid_amend.returncode == 1
      and os.path.getsize(os.path.join(life, "LOG.txt")) == before_invalid,
      "amend accepted a missing ID or changed the log")
# supersession outlives compression: a summary-block target is a first-class
# lifecycle record, and every raw memory inside the range shows it later
block_amend = run("amend", "0-1", "Superseding the summarized pair.",
                  store=life)
check(block_amend.returncode == 0 and "Amended #0-#1" in block_amend.stdout,
      "amend of a block was refused: " + block_amend.stdout + block_amend.stderr)
for inner in ("0", "1"):
    covered = run("show", inner, store=life)
    check("Amends #0-#1: Superseding the summarized pair." in covered.stdout,
          "show %s does not list the covering block supersession:\n"
          % inner + covered.stdout)
check(run("amend", "3-9", "Not a block.", store=life).returncode == 1,
      "amend accepted a non-block range")
check(run("amend", "1024-2047", "Beyond the log.", store=life).returncode == 1,
      "amend accepted a block beyond the log")
# a block is also a first-class question: its summary and what supersedes it
block_shown = run("show", "0-1", store=life)
check(block_shown.returncode == 0
      and block_shown.stdout.startswith("#0-1 not compressed yet")
      and "Amends #0-#1: Superseding the summarized pair." in
          block_shown.stdout,
      "show of a block did not list its supersession:\n" + block_shown.stdout)
check(run("show", "3-9", store=life).returncode == 1,
      "show accepted a non-block range")
# --fit works where the budget is tightest: after the lifecycle prefix
long_fix = ("delta echo foxtrot " * 20).strip()
fit_amend = run("amend", "--fit", "0-1", long_fix, store=life)
check(fit_amend.returncode == 0 and "Fitted: cut the last" in fit_amend.stdout,
      "amend --fit did not trim and report: "
      + fit_amend.stdout + fit_amend.stderr)
fitted_entry = cli.log_get(life, cli.log_len(life) - 1)[2]
fitted_body = fitted_entry.split(" ", 1)[1] \
    if fitted_entry.startswith("@") else fitted_entry
check(len(fitted_body.encode()) <= 280
      and fitted_body.startswith("Amends #0-#1: delta echo")
      and not fitted_body.endswith(" "),
      "amend --fit produced an invalid lifecycle record: %r" % fitted_body)
check(run("amend", "0", long_fix, store=life).returncode == 1,
      "an over-long amend without --fit was accepted")
check(run("retract", "--fit", "0", long_fix, store=life).returncode == 0,
      "retract --fit was refused")

# doctor names the active session tag and its source
diagnosed_tag = run("doctor", store=life)
check("Session tag:   @t1 (OPTMEM_SESSION)" in diagnosed_tag.stdout,
      "doctor does not report the session tag:\n" + diagnosed_tag.stdout)
# shared multiplexer/daemon ancestors are never a session identity
for shared in ("tmux", "tmux: server", "screen", "sshd", "sshd-session",
               "/usr/sbin/sshd", "bash", "pwsh.exe", "python3"):
    check(cli._transient_ancestor(shared),
          "%r was accepted as a stable session ancestor" % shared)
check(not cli._transient_ancestor("node.exe")
      and not cli._transient_ancestor("claude"),
      "a harness process was wrongly treated as transient")
check(run("amend", "0", "   ", store=life).returncode == 1
      and run("retract", "0", "   ", store=life).returncode == 1,
      "lifecycle commands accepted an empty replacement/reason")

# ---- provenance -------------------------------------------------------
# Every written entry is stamped with the session's @tag inside its payload,
# so wake, recall, show, and nap prompts display authorship for free, and a
# parallel session's entries are visibly its testimony.
check(cli.log_get(life, 0)[2] == "@t1 Mutation requests may be retried.",
      "a note was not stamped with the session tag: "
      + cli.log_get(life, 0)[2])
prov = tempfile.mkdtemp(prefix="optmem-provenance-")
os.makedirs(os.path.join(prov, "TREE"))
open(os.path.join(prov, "LOG.txt"), "wb").close()
other = subprocess.run(
    memo + ["note", "Written by a parallel session."],
    capture_output=True, text=True,
    env=dict(os.environ, MEMORY_DIR=prov, OPTMEM_SESSION="other-2"))
check(other.returncode == 0
      and cli.log_get(prov, 0)[2]
      == "@other-2 Written by a parallel session.",
      "a second session's tag did not distinguish its entries: "
      + other.stdout + other.stderr)
run("note", "Written by the test session.", store=prov)
both = run("recall", "session", store=prov)
check("@other-2 " in both.stdout and "@t1 " in both.stdout,
      "recall does not show which session wrote each entry:\n" + both.stdout)
# a hostile or overlong OPTMEM_SESSION is sanitized, never trusted
weird = subprocess.run(
    memo + ["note", "Sanitized tag."], capture_output=True, text=True,
    env=dict(os.environ, MEMORY_DIR=prov,
             OPTMEM_SESSION="a b/c\\d:e|f&g<h>i j-k_9longtail"))
check(weird.returncode == 0
      and cli.log_get(prov, 2)[2].startswith("@abcdefgh "),
      "OPTMEM_SESSION was not sanitized: " + cli.log_get(prov, 2)[2])
# without OPTMEM_SESSION the tag is derived, or dropped -- never a failure
derived_env = {k: v for k, v in os.environ.items() if k != "OPTMEM_SESSION"}
derived_env["MEMORY_DIR"] = prov
derived = subprocess.run(memo + ["note", "Derived-tag entry."],
                         capture_output=True, text=True, env=derived_env)
check(derived.returncode == 0 and "Saved as #3" in derived.stdout
      and re.fullmatch(r"(@[\w-]{1,8} )?Derived-tag entry\.",
                       cli.log_get(prov, 3)[2]),
      "a note without OPTMEM_SESSION failed or wrote a malformed tag: "
      + derived.stdout + derived.stderr + cli.log_get(prov, 3)[2])
# a maximum-length entry still fits the fixed-width record with its tag
maximal = "m" * 280
full_note = run("note", maximal, store=prov)
check(full_note.returncode == 0
      and cli.log_get(prov, 4)[2].endswith(maximal),
      "a maximum-length note was refused or truncated by tagging")

# ---- note --fit -------------------------------------------------------
wordy = ("alpha bravo charlie " * 20).strip()   # 399 bytes of whole words
fitted = run("note", "--fit", wordy, store=prov)
check(fitted.returncode == 0 and "Fitted: cut the last" in fitted.stdout
      and "word boundary" in fitted.stdout,
      "note --fit did not trim and report: " + fitted.stdout + fitted.stderr)
kept = cli.log_get(prov, 5)[2]
kept_text = kept.split(" ", 1)[1] if kept.startswith("@") else kept
check(len(kept_text.encode()) <= 280 and not kept_text.endswith(" ")
      and wordy.startswith(kept_text)
      and wordy[len(kept_text):].startswith(" "),
      "note --fit did not cut at a word boundary: %r" % kept_text)
check(run("note", "--fit", "under the limit already", store=prov)
      .stdout.count("Fitted") == 0,
      "note --fit warned about a line that already fits")

# ---- backfilled dates -------------------------------------------------
# --date backfills a real past day; the log stays chronologically ordered.
dated = tempfile.mkdtemp(prefix="optmem-dated-")
os.makedirs(os.path.join(dated, "TREE"))
open(os.path.join(dated, "LOG.txt"), "wb").close()
check(run("note", "--date", "2026-01-05", "Backfilled fact.",
          store=dated).returncode == 0
      and cli.log_get(dated, 0)[1] == "2026-01-05",
      "note --date did not backfill the entry date")
check(run("note", "--date", "2026-01-04", "Regressing.",
          store=dated).returncode == 1,
      "--date was allowed to precede the newest memory")
check(run("note", "--date", "2099-01-01", "Future.",
          store=dated).returncode == 1,
      "--date accepted a future day")
check(run("note", "--date", "2026-02-30", "Unreal.",
          store=dated).returncode == 1,
      "--date accepted an impossible day")
check(run("note", "--date=2026-01-06", "Equals form.",
          store=dated).returncode == 0
      and cli.log_get(dated, 1)[1] == "2026-01-06",
      "note --date=VALUE form failed")
check(run("amend", "--date", "2026-01-07", "0", "Corrected later.",
          store=dated).returncode == 0
      and cli.log_get(dated, 2)[1] == "2026-01-07",
      "amend --date did not backfill the lifecycle date")
check(run("note", "Default day after a backfill.",
          store=dated).returncode == 0,
      "today's default date failed after backfilled entries")

# --date also takes a timestamp; chronology is enforced by DAY, so bare
# dates and timestamped moments coexist and time of day is informational
timed = tempfile.mkdtemp(prefix="optmem-timed-")
os.makedirs(os.path.join(timed, "TREE"))
open(os.path.join(timed, "LOG.txt"), "wb").close()
check(run("note", "--date", "2026-03-01T10:30", "Timestamped.",
          store=timed).returncode == 0
      and cli.log_get(timed, 0)[1] == "2026-03-01T10:30",
      "note --date did not store a timestamp")
check(run("note", "--date", "2026-03-01", "Bare date, same day.",
          store=timed).returncode == 0,
      "a bare date was refused after a same-day timestamp")
check(run("note", "--date", "2026-03-01T09:00:05", "Earlier same day.",
          store=timed).returncode == 0,
      "a same-day earlier timestamp was refused (time is informational)")
check(run("note", "--date", "2026-02-28T23:59", "Earlier day.",
          store=timed).returncode == 1,
      "--date accepted an earlier day via timestamp")
check(run("note", "--date", "2026-03-01T25:00", "Bad hour.",
          store=timed).returncode == 1,
      "--date accepted hour 25")
check(run("note", "--date", "2026-03-01T10:00+02:00", "Offset.",
          store=timed).returncode == 1,
      "--date accepted a timezone offset")
timed_errors, _, _, _ = cli._verify_store(timed)
check(not timed_errors,
      "deep verification rejected timestamped records: %r" % timed_errors)
timed_export = os.path.join(timed, "timed.txt")
run("export", timed_export, store=timed)
timed_restore = tempfile.mkdtemp(prefix="optmem-timed-restore-")
os.makedirs(os.path.join(timed_restore, "TREE"))
open(os.path.join(timed_restore, "LOG.txt"), "wb").close()
check(run("import", timed_export, store=timed_restore).returncode == 0
      and cli.log_get(timed_restore, 0)[1] == "2026-03-01T10:30",
      "timestamped records did not survive an export/import round trip")

# ---- hooks ------------------------------------------------------------
# OPTMEM_HOOK_PRE rewrites or refuses before validation; OPTMEM_HOOK_POST
# observes the durable record. Both are environment commands, never store
# files, so a synced store can never execute code.
hookdir = tempfile.mkdtemp(prefix="optmem-hooks-")
if " " not in sys.executable:
    hook_runner = sys.executable
elif os.name == "nt":
    hook_runner = "py -3"
else:
    hook_runner = "'%s'" % sys.executable
with open(os.path.join(hookdir, "pre_upper.py"), "w") as f:
    f.write("import sys\nsys.stdout.write(sys.stdin.read().strip().upper())\n")
with open(os.path.join(hookdir, "pre_veto.py"), "w") as f:
    f.write("import sys\nsys.stderr.write('secret detected')\nsys.exit(3)\n")
seen_txt = os.path.join(hookdir, "post_seen.txt")
with open(os.path.join(hookdir, "post_record.py"), "w") as f:
    f.write("import sys\nopen(%r, 'a', encoding='utf-8')"
            ".write(sys.stdin.read())\n" % seen_txt)
hooked = tempfile.mkdtemp(prefix="optmem-hookstore-")
os.makedirs(os.path.join(hooked, "TREE"))
open(os.path.join(hooked, "LOG.txt"), "wb").close()
os.environ["OPTMEM_HOOK_PRE"] = '%s "%s"' % (
    hook_runner, os.path.join(hookdir, "pre_upper.py"))
os.environ["OPTMEM_HOOK_POST"] = '%s "%s"' % (
    hook_runner, os.path.join(hookdir, "post_record.py"))
try:
    r = run("note", "hooked entry", store=hooked)
    check(r.returncode == 0
          and cli.log_get(hooked, 0)[2] == "@t1 HOOKED ENTRY",
          "the pre hook did not rewrite the memory: "
          + cli.log_get(hooked, 0)[2] if cli.log_len(hooked) else r.stderr)
    observed = open(seen_txt, encoding="utf-8").read()
    check(observed.startswith("#0 ") and "@t1 HOOKED ENTRY" in observed,
          "the post hook did not receive the written record: %r" % observed)
    os.environ["OPTMEM_HOOK_PRE"] = '%s "%s"' % (
        hook_runner, os.path.join(hookdir, "pre_veto.py"))
    r = run("note", "should be refused", store=hooked)
    check(r.returncode == 1 and "refused" in r.stderr
          and "secret detected" in r.stderr and cli.log_len(hooked) == 1,
          "a vetoing pre hook did not refuse the write: "
          + r.stdout + r.stderr)
finally:
    os.environ.pop("OPTMEM_HOOK_PRE", None)
    os.environ.pop("OPTMEM_HOOK_POST", None)

# OPTMEM_HOOK_SHOW filters displayed memory lines only: protocol text is
# untouched, and any failure falls back to the raw lines.
with open(os.path.join(hookdir, "show_mark.py"), "w") as f:
    f.write("import sys\n"
            "for line in sys.stdin.read().splitlines():\n"
            "    sys.stdout.write('<<' + line + '>>\\n')\n")
with open(os.path.join(hookdir, "show_drop.py"), "w") as f:
    f.write("import sys\nsys.stdin.read()\nsys.stdout.write('one line\\n')\n")
os.environ["OPTMEM_HOOK_SHOW"] = '%s "%s"' % (
    hook_runner, os.path.join(hookdir, "show_mark.py"))
try:
    woke_hooked = run("wake", store=hooked)
    check(woke_hooked.returncode == 0
          and "<<#0 " in woke_hooked.stdout
          and "HOOKED ENTRY>>" in woke_hooked.stdout
          and woke_hooked.stdout.rstrip().endswith("You are awake."),
          "the show hook did not filter wake lines, or touched protocol "
          "text:\n" + woke_hooked.stdout)
    shown_hooked = run("show", "0", store=hooked)
    check(shown_hooked.stdout.startswith("<<#0 "),
          "the show hook did not filter show output:\n" + shown_hooked.stdout)
    recalled_hooked = run("recall", "HOOKED", store=hooked)
    check("<<#0 " in recalled_hooked.stdout
          and "1 match." in recalled_hooked.stdout,
          "the show hook did not filter recall output, or altered its "
          "protocol summary:\n" + recalled_hooked.stdout)
    # a hook that changes the line count must not be able to lose records
    run("note", "second entry", store=hooked)
    os.environ["OPTMEM_HOOK_SHOW"] = '%s "%s"' % (
        hook_runner, os.path.join(hookdir, "show_drop.py"))
    dropped_hooked = run("zoom", "0-1", store=hooked)
    check(dropped_hooked.returncode == 0
          and "#0 " in dropped_hooked.stdout
          and "#1 " in dropped_hooked.stdout
          and "<<" not in dropped_hooked.stdout
          and "showing raw lines" in dropped_hooked.stderr,
          "a line-dropping show hook was allowed to lose records:\n"
          + dropped_hooked.stdout + dropped_hooked.stderr)
finally:
    os.environ.pop("OPTMEM_HOOK_SHOW", None)

portable = os.path.join(life, "portable.txt")
inspection = os.path.join(life, "inspection.txt")
check(run("export", portable, store=life).returncode == 0
      and open(portable, encoding="utf-8").read().startswith(
          datetime.date.today().isoformat() + " @t1 Mutation"),
      "default export is not portable or lost the provenance tag")
check(run("export", "--with-ids", inspection, store=life).returncode == 0
      and open(inspection, encoding="utf-8").read().startswith("#0 "),
      "inspection export omitted IDs")
check(run("export", os.path.join(life, "LOG.txt"), store=life).returncode == 1,
      "export was allowed to overwrite authoritative LOG.txt")

restored = tempfile.mkdtemp(prefix="optmem-restored-")
os.makedirs(os.path.join(restored, "TREE"))
open(os.path.join(restored, "LOG.txt"), "wb").close()
dry = run("import", "--dry-run", portable, store=restored)
check(dry.returncode == 0 and "Valid OptMem import" in dry.stdout
      and "Amendments:    3" in dry.stdout
      and "Retractions:   2" in dry.stdout and cli.log_len(restored) == 0,
      "import --dry-run wrote data or omitted lifecycle diagnostics:\n"
      + dry.stdout + dry.stderr)
loaded = run("import", portable, store=restored)
check(loaded.returncode == 0 and cli.log_len(restored) == 7
      and cli.log_get(restored, 1)[2].startswith("@t1 Amends #0:"),
      "portable import did not preserve ordered IDs and provenance")
loaded_bytes = open(os.path.join(restored, "LOG.txt"), "rb").read()
second_import = run("import", portable, store=restored)
check(second_import.returncode == 1 and "nonempty store" in second_import.stderr
      and open(os.path.join(restored, "LOG.txt"), "rb").read() == loaded_bytes,
      "bootstrap-only import appended into a nonempty store")

bad_import = os.path.join(life, "bad-reference.txt")
with open(bad_import, "w", encoding="utf-8") as f:
    f.write("2026-07-27 Amends #1: points forward\n")
bad_store = tempfile.mkdtemp(prefix="optmem-bad-import-")
os.makedirs(os.path.join(bad_store, "TREE"))
open(os.path.join(bad_store, "LOG.txt"), "wb").close()
bad_dry = run("import", "--dry-run", bad_import, store=bad_store)
check(bad_dry.returncode == 1 and "Invalid OptMem import" in bad_dry.stderr
      and cli.log_len(bad_store) == 0,
      "import accepted a forward lifecycle reference")
cli.log_append(bad_store, [("2026-07-27", "Amends #9: invalid raw ref")])
bad_errors, _, _, _ = cli._verify_store(bad_store)
check(any("forward or to itself" in problem for problem in bad_errors),
      "deep verification missed an invalid lifecycle reference")

link_store = tempfile.mkdtemp(prefix="optmem-link-store-")
os.makedirs(os.path.join(link_store, "TREE"))
link_tested = False
try:
    os.symlink(portable, os.path.join(link_store, "LOG.txt"))
    link_tested = True
except (OSError, NotImplementedError):
    pass
if link_tested:
    link_errors, _, _, _ = cli._verify_store(link_store)
    check(link_errors == ["LOG.txt is a symbolic link"]
          and "symbolic link" in cli.memory_status(link_store),
          "deep verification followed a symbolic-link authoritative log")

# A batch contains one dense run at the smallest pending level. Applying is
# all-or-nothing and one fsync, so large imports need far fewer agent turns.
batch = run("nap", "--batch", "3", store=restored)
check(batch.returncode == 0
      and all(("%d-%d:" % pair) in batch.stdout
              for pair in ((0, 1), (2, 3)))
      and "<range><TAB><one-line summary>" in batch.stdout,
      "nap --batch did not print independent ready jobs:\n" + batch.stdout)
stale_file = os.path.join(restored, "stale.tsv")
with open(stale_file, "w", encoding="utf-8") as f:
    f.write("2-3\tsecond before first\n")
stale = run("nap", "--apply", stale_file, store=restored)
check(stale.returncode == 1
      and cli.count(cli.tree_path(restored, 2), cli.TREE_REC) == 0,
      "a stale batch partially changed the tree")
batch_file = os.path.join(restored, "summaries.tsv")
with open(batch_file, "w", encoding="utf-8") as f:
    f.write("0-1\tFinal retry policy allows only idempotent reads.\n")
    f.write("2-3\tThe old policy was retracted; #0 and #1 anchor rationale.\n")
applied = run("nap", "--apply", batch_file, store=restored)
check(applied.returncode == 0
      and cli.count(cli.tree_path(restored, 2), cli.TREE_REC) == 2,
      "nap --apply did not atomically save the batch")

batch_budget = tempfile.mkdtemp(prefix="optmem-batch-budget-")
os.makedirs(os.path.join(batch_budget, "TREE"))
open(os.path.join(batch_budget, "LOG.txt"), "wb").close()
cli.log_append(batch_budget, [
    ("2026-07-27", ("%03d " % index) + "x" * 250)
    for index in range(128)
])
bounded_batch = run("nap", "--batch", "64", store=batch_budget)
printed_jobs = re.findall(r"(?m)^\d+-\d+:$", bounded_batch.stdout)
check(bounded_batch.returncode == 0 and 0 < len(printed_jobs) < 64
      and len(bounded_batch.stdout.encode("utf-8")) <= cli.PART_CHARS
      and len(bounded_batch.stdout.splitlines()) <= cli.PART_LINES,
      "nap --batch exceeded transport limits instead of fitting fewer jobs")
oversized_batch = os.path.join(batch_budget, "oversized.tsv")
with open(oversized_batch, "wb") as f:
    f.truncate(cli.MAX_NAP_APPLY_BYTES + 1)
oversized_apply = run("nap", "--apply", oversized_batch, store=batch_budget)
check(oversized_apply.returncode == 1 and "too large" in oversized_apply.stderr,
      "nap --apply accepted an unbounded input file")

errors, warnings, records_, summaries = cli._verify_store(restored)
check(not errors and records_ == 7 and summaries == 2,
      "deep verification rejected a healthy lifecycle store: %r" % errors)
deep = run("doctor", "--deep", store=restored)
check(deep.returncode == 0 and "Deep verification" in deep.stdout
      and "Result:         OK" in deep.stdout,
      "doctor --deep did not report a healthy store:\n"
      + deep.stdout + deep.stderr)

# Redaction is the only raw rewrite: it preserves width, ID, and date, removes
# the payload, and invalidates every summary that could retain the text.
before_redact = os.path.getsize(os.path.join(restored, "LOG.txt"))
confirm = run("redact", "0", store=restored)
check(confirm.returncode == 1 and "Run again with --force" in confirm.stderr,
      "redact did not require explicit force")
redacted = run("redact", "0", "--force", store=restored)
log_after_redact = open(os.path.join(restored, "LOG.txt"), "rb").read()
check(redacted.returncode == 0 and cli.log_get(restored, 0)[2] ==
      "[REDACTED BY USER]"
      and os.path.getsize(os.path.join(restored, "LOG.txt")) == before_redact
      and b"Mutation requests may be retried." not in log_after_redact
      and cli.count(cli.tree_path(restored, 2), cli.TREE_REC) == 0,
      "redact did not erase in place and invalidate derived summaries:\n"
      + redacted.stdout + redacted.stderr)
check(cli.tree_put(restored, 0, 2, "stale summary retaining old secret"),
      "could not arrange interrupted-redaction regression")
redacted_retry = run("redact", "0", "--force", store=restored)
check(redacted_retry.returncode == 0
      and "already redacted; rechecked its derived caches" in
          redacted_retry.stdout
      and cli.count(cli.tree_path(restored, 2), cli.TREE_REC) == 0,
      "retrying redaction did not finish derived-cache cleanup:\n"
      + redacted_retry.stdout + redacted_retry.stderr)
errors, _, _, _ = cli._verify_store(restored)
check(not errors, "deep verification rejected a redacted store: %r" % errors)

qmd_redact = tempfile.mkdtemp(prefix="optmem-redact-qmd-")
os.makedirs(os.path.join(qmd_redact, "TREE"))
open(os.path.join(qmd_redact, "LOG.txt"), "wb").close()
cli.log_append(qmd_redact, [
    ("2026-07-27", "secret payload copied into QMD"),
    ("2026-07-27", "ordinary second record"),
])
cli._qmd_mark(qmd_redact, "enabled")
cli._qmd_sync_projection(qmd_redact)
qmd_redact_calls = []
real_remove_collection = cli._qmd_remove_collection
real_index_projection = cli._qmd_index_projection
cli._qmd_remove_collection = lambda store: (
    qmd_redact_calls.append("remove") or None)


def rebuild_redacted_projection(store, verify=False):
    qmd_redact_calls.append("rebuild")
    cli._qmd_sync_projection(store)
    return cli.log_len(store), 1


cli._qmd_index_projection = rebuild_redacted_projection
qmd_redacted = run("redact", "0", "--force", store=qmd_redact)
cli._qmd_remove_collection = real_remove_collection
cli._qmd_index_projection = real_index_projection
qmd_segment = open(os.path.join(
    qmd_redact, "QMD", "00000000-00000015.md"), encoding="utf-8").read()
check(qmd_redacted.returncode == 0
      and qmd_redact_calls == ["remove", "rebuild"]
      and "secret payload" not in qmd_segment
      and "[REDACTED BY USER]" in qmd_segment,
      "redaction left sensitive text in an enabled QMD projection:\n"
      + qmd_redacted.stdout + qmd_redacted.stderr)

shutil.rmtree(life)
shutil.rmtree(restored)
shutil.rmtree(bad_store)
shutil.rmtree(qmd_redact)
shutil.rmtree(batch_budget)
shutil.rmtree(link_store)


# QMD is an explicitly enabled, disposable semantic index. Projection files
# contain raw entries, but every result is resolved back through LOG.txt.
qmd_store = tempfile.mkdtemp(prefix="optmem-qmd-")
os.makedirs(os.path.join(qmd_store, "TREE"))
open(os.path.join(qmd_store, "LOG.txt"), "wb").close()
cli.log_append(qmd_store, [
    ("2026-07-%02d" % (1 + i // 4), "qmd canonical memory %d" % i)
    for i in range(35)
])
check(not os.path.exists(os.path.join(qmd_store, "QMD")),
      "a QMD projection appeared before opt-in")
run("note", "recorded before QMD was enabled", store=qmd_store)
check(not os.path.exists(os.path.join(qmd_store, "QMD")),
      "memo note touched QMD while the integration was disabled")

real_qmd_run = cli._qmd_run
qmd_calls, qmd_collections = [], set()
qmd_fail_query = [False]
qmd_fail_embed = [False]


def fake_qmd_run(args, timeout=120):
    args = list(args)
    qmd_calls.append((args, timeout))
    if args == ["--version"]:
        return "qmd 2.5.3"
    if args[:2] == ["collection", "show"]:
        if args[2] not in qmd_collections:
            raise cli.QmdError("Collection not found: " + args[2])
        return "Collection: %s\n  Path:     %s" % (args[2], cli.qmd_dir(qmd_store))
    if args[:2] == ["collection", "add"]:
        qmd_collections.add(args[args.index("--name") + 1])
        return "created"
    if args[:2] == ["collection", "remove"]:
        qmd_collections.discard(args[2])
        return "removed"
    if args and args[0] == "embed" and qmd_fail_embed[0]:
        raise cli.QmdError("embedding interrupted")
    if args and args[0] in ("context", "update", "embed"):
        return "ok"
    if args and args[0] == "query":
        if qmd_fail_query[0]:
            raise cli.QmdError("model temporarily unavailable")
        collection = cli.qmd_collection(qmd_store)
        return json.dumps([
            {
                "file": "qmd://%s/00000016-00000031.md?index=optmem"
                        % collection,
                "score": 0.82,
                "line": 5,
                "snippet": "#18 text supplied by QMD must not be trusted",
            },
            {
                "file": "qmd://%s/00000032-00000047.md?index=optmem"
                        % collection,
                "score": 0.61,
                "line": 7,
                "snippet": "#35 another untrusted projection snippet",
            },
        ])
    raise cli.QmdError("unexpected fake qmd call: %r" % args)


cli._qmd_run = fake_qmd_run
enabled = run("qmd", "enable", store=qmd_store)
check(enabled.returncode == 0 and "enabled" in enabled.stdout,
      "memo qmd enable failed: " + enabled.stdout + enabled.stderr)
fallback_default = run("qmd", "config", store=qmd_store)
check(fallback_default.returncode == 0 and "FALLBACK=off" in
      fallback_default.stdout,
      "QMD semantic fallback was not safely off by default")
projection = os.path.join(qmd_store, "QMD")
segments = sorted(name for name in os.listdir(projection)
                  if name.endswith(".md"))
check(segments == ["00000000-00000015.md", "00000016-00000031.md",
                   "00000032-00000047.md"],
      "QMD did not create fixed 16-memory segments: %r" % segments)
with open(os.path.join(projection, "state"), encoding="utf-8") as f:
    qmd_state = json.load(f)
check(qmd_state == {"format": 1, "logRecords": 36, "segmentSize": 16},
      "QMD projection state is not minimal and deterministic: %r" % qmd_state)
first_segment = open(os.path.join(projection, segments[0]),
                     encoding="utf-8").read()
check(first_segment.startswith("#0 2026-07-01 @t1 qmd canonical memory 0\n\n")
      and "#15 " in first_segment and "\x00" not in first_segment,
      "QMD segment is not an unchanged logical projection of raw entries")

completed_before = {
    name: open(os.path.join(projection, name), "rb").read()
    for name in segments[:2]
}
partial_path = os.path.join(projection, segments[2])
partial_before = open(partial_path, "rb").read()
run("note", "appended lazily after QMD enable", store=qmd_store)
check(open(partial_path, "rb").read() == partial_before,
      "memo note synchronously rewrote the QMD projection")

qmd_calls.clear()
semantic = run("recall", "--semantic", "why did policy change?",
               store=qmd_store)
check(semantic.returncode == 0
      and "Semantic matches in selected memory" in semantic.stdout
      and "#18 2026-07-05 @t1 qmd canonical memory 18" in semantic.stdout
      and "#35 " in semantic.stdout
      and "text supplied by QMD must not be trusted" not in semantic.stdout,
      "semantic recall did not resolve QMD hits through canonical LOG.txt:\n"
      + semantic.stdout + semantic.stderr)
called_commands = [call[0][0] for call in qmd_calls]
check("update" in called_commands and "embed" in called_commands
      and "query" in called_commands,
      "semantic recall did not lazily update, embed, and query: %r" % qmd_calls)
check(all(open(os.path.join(projection, name), "rb").read() ==
          completed_before[name] for name in segments[:2])
      and b"appended lazily after QMD enable" in open(partial_path, "rb").read(),
      "lazy sync changed a completed segment or missed the partial segment")
check(not os.path.exists(os.path.join(projection, "dirty")),
      "successful QMD synchronization left its retry marker behind")

qmd_calls.clear()
semantic_again = run("recall", "--semantic", "policy", store=qmd_store)
called_commands = [call[0][0] for call in qmd_calls]
check(semantic_again.returncode == 0 and called_commands.count("query") == 1
      and "update" not in called_commands and "embed" not in called_commands,
      "an unchanged projection was unnecessarily re-indexed: %r" % qmd_calls)

run("note", "prepaid by the explicit QMD sync command", store=qmd_store)
qmd_calls.clear()
synced = run("qmd", "sync", store=qmd_store)
called_commands = [call[0][0] for call in qmd_calls]
check(synced.returncode == 0 and "current" in synced.stdout
      and "update" in called_commands and "embed" in called_commands
      and "query" not in called_commands,
      "memo qmd sync did not prepay projection and embedding work:\n"
      + synced.stdout + synced.stderr + repr(qmd_calls))
qmd_calls.clear()
verified_sync = run("qmd", "sync", store=qmd_store)
check(verified_sync.returncode == 0
      and any(call[0][0] == "update" for call in qmd_calls)
      and any(call[0][0] == "embed" for call in qmd_calls),
      "memo qmd sync did not verify QMD after an unchanged projection")

qmd_calls.clear()
fast_semantic = run("recall", "--semantic", "--fast", "policy",
                    store=qmd_store)
query_calls = [call[0] for call in qmd_calls if call[0][0] == "query"]
check(fast_semantic.returncode == 0
      and "fast, no reranking" in fast_semantic.stdout
      and len(query_calls) == 1 and "--no-rerank" in query_calls[0]
      and not any(call[0][0] in ("update", "embed") for call in qmd_calls),
      "semantic --fast did not skip reranking or reuse explicit sync:\n"
      + fast_semantic.stdout + fast_semantic.stderr + repr(qmd_calls))
check(run("recall", "--fast", "policy", store=qmd_store).returncode == 1,
      "recall accepted --fast without --semantic")

fallback_on = run("qmd", "config", "FALLBACK=on", store=qmd_store)
check(fallback_on.returncode == 0 and "FALLBACK=on" in fallback_on.stdout
      and os.path.isfile(os.path.join(projection, "fallback")),
      "memo qmd config did not enable per-scope fallback")
qmd_calls.clear()
exact_with_fallback = run("recall", "canonical memory 18", store=qmd_store)
check(exact_with_fallback.returncode == 0 and not qmd_calls,
      "configured fallback invoked QMD despite an exact result")
saved_fff_recall = cli.fff_recall
cli.fff_recall = lambda store, query, limit=None: ([], None)
qmd_calls.clear()
semantic_fallback = run(
    "recall", "meaning that exact and fuzzy cannot find", store=qmd_store)
cli.fff_recall = saved_fff_recall
check(semantic_fallback.returncode == 0
      and "Trying QMD semantic fallback" in semantic_fallback.stdout
      and "Semantic matches in selected memory" in semantic_fallback.stdout
      and any(call[0][0] == "query" for call in qmd_calls),
      "configured zero-result recall did not escalate to QMD:\n"
      + semantic_fallback.stdout + semantic_fallback.stderr + repr(qmd_calls))
fallback_off = run("qmd", "config", "FALLBACK=off", store=qmd_store)
check(fallback_off.returncode == 0 and "FALLBACK=off" in fallback_off.stdout
      and not os.path.exists(os.path.join(projection, "fallback")),
      "memo qmd config did not disable fallback")
# Leave it on through disable: the cheap retained projection also retains the
# preference, but it must remain inactive until QMD is enabled again.
run("qmd", "config", "FALLBACK=on", store=qmd_store)

run("note", "forces an interrupted QMD embedding retry", store=qmd_store)
qmd_fail_embed[0] = True
interrupted_sync = run("recall", "--semantic", "policy", store=qmd_store)
qmd_fail_embed[0] = False
check(interrupted_sync.returncode == 1
      and os.path.exists(os.path.join(projection, "dirty")),
      "an interrupted embedding lost the QMD dirty retry marker")
qmd_calls.clear()
retried_sync = run("recall", "--semantic", "policy", store=qmd_store)
check(retried_sync.returncode == 0
      and any(call[0][0] == "update" for call in qmd_calls)
      and any(call[0][0] == "embed" for call in qmd_calls)
      and not os.path.exists(os.path.join(projection, "dirty")),
      "semantic recall did not retry an interrupted QMD synchronization")

qmd_fail_query[0] = True
failed_semantic = run("recall", "--semantic", "policy", store=qmd_store)
qmd_fail_query[0] = False
check(failed_semantic.returncode == 1
      and "Semantic recall failed" in failed_semantic.stderr
      and "Exact and FFF fuzzy recall are still available" in
          failed_semantic.stderr,
      "a QMD failure was not isolated and actionable:\n"
      + failed_semantic.stdout + failed_semantic.stderr)
check("qmd canonical memory 18" in
      run("recall", "canonical memory 18", store=qmd_store).stdout,
      "QMD failure damaged ordinary exact recall")
qmd_calls.clear()
ordinary_recall = run("recall", "canonical memory 18", store=qmd_store)
check(ordinary_recall.returncode == 0 and not qmd_calls,
      "ordinary recall invoked QMD while semantic mode was not requested")
check(run("recall", "--fuzzy", "--semantic", "x",
          store=qmd_store).returncode == 1,
      "recall accepted conflicting fuzzy and semantic modes")
check(run("recall", "--semantic", "--limit", "2", "x",
          store=qmd_store).returncode == 1,
      "semantic recall silently accepted exact-recall controls")

wake_qmd_store = tempfile.mkdtemp(prefix="optmem-qmd-wake-")
os.makedirs(os.path.join(wake_qmd_store, "TREE"))
open(os.path.join(wake_qmd_store, "LOG.txt"), "wb").close()
cli.log_append(wake_qmd_store, [("2026-07-27", "one wake capability memory")])
cli._qmd_mark(wake_qmd_store, "enabled")
enabled_wake = run("wake", store=wake_qmd_store)
check(enabled_wake.returncode == 0
      and "Semantic recall is available:" in enabled_wake.stdout
      and 'recall --semantic "<meaning>"' in enabled_wake.stdout,
      "wake omitted the enabled semantic-recall capability:\n"
      + enabled_wake.stdout + enabled_wake.stderr)
os.unlink(os.path.join(wake_qmd_store, "QMD", "enabled"))
disabled_wake = run("wake", store=wake_qmd_store)
check("Semantic recall is available:" not in disabled_wake.stdout,
      "wake advertised semantic recall while QMD was disabled")
shutil.rmtree(wake_qmd_store)

# Restoring an older backup removes impossible trailing segments and rewrites
# only the now-current partial segment.
with cli.locked(qmd_store):
    with open(os.path.join(qmd_store, "LOG.txt"), "r+b") as f:
        f.truncate(20 * cli.LOG_REC)
changed, qmd_records, qmd_segment_count = cli._qmd_sync_projection(qmd_store)
check(changed and qmd_records == 20 and qmd_segment_count == 2
      and sorted(name for name in os.listdir(projection)
                 if name.endswith(".md")) ==
          ["00000000-00000015.md", "00000016-00000031.md"],
      "QMD projection did not recover from an older restored log")
restored_log = open(os.path.join(qmd_store, "LOG.txt"), "rb").read()

qmd_calls.clear()
rebuilt = run("qmd", "rebuild", store=qmd_store)
check(rebuilt.returncode == 0 and "Rebuilt" in rebuilt.stdout
      and any(call[0][:2] == ["collection", "remove"] for call in qmd_calls)
      and any(call[0][:2] == ["collection", "add"] for call in qmd_calls)
      and any(call[0][0] == "embed" for call in qmd_calls),
      "memo qmd rebuild did not recreate the derived collection:\n"
      + rebuilt.stdout + rebuilt.stderr + repr(qmd_calls))

disabled = run("qmd", "disable", store=qmd_store)
check(disabled.returncode == 0 and "disabled" in disabled.stdout
      and os.path.isdir(projection)
      and not os.path.exists(os.path.join(projection, "enabled")),
      "qmd disable did not retain the disposable projection")
disabled_semantic = run("recall", "--semantic", "policy", store=qmd_store)
check(disabled_semantic.returncode == 1
      and "not enabled" in disabled_semantic.stderr,
      "semantic recall ran while QMD was disabled")
qmd_status = run("qmd", "status", store=qmd_store)
check(qmd_status.returncode == 0 and "Integration:     disabled" in
      qmd_status.stdout and "Fallback:        on (inactive while disabled)" in
      qmd_status.stdout and "qmd 2.5.3" in qmd_status.stdout,
      "memo qmd status did not explain disabled retained state:\n"
      + qmd_status.stdout + qmd_status.stderr)

cli._qmd_run = lambda args, timeout=120: (
    (_ for _ in ()).throw(cli.QmdError("qmd temporarily unavailable")))
failed_reenable = run("qmd", "enable", store=qmd_store)
check(failed_reenable.returncode == 1 and os.path.isdir(projection),
      "a failed re-enable deleted the retained disposable projection")
cli._qmd_run = fake_qmd_run
run("qmd", "enable", store=qmd_store)
purged = run("qmd", "disable", "--purge", store=qmd_store)
check(purged.returncode == 0 and "Removed the derived projection" in
      purged.stdout and not os.path.exists(projection),
      "qmd disable --purge left derived projection data")
cli._qmd_run = lambda args, timeout=120: (
    (_ for _ in ()).throw(cli.QmdError("qmd is not installed")))
missing_qmd_status = run("qmd", "status", store=qmd_store)
check(missing_qmd_status.returncode == 0
      and "Exact/FFF recall remains available" in missing_qmd_status.stdout
      and "npm install -g @tobilu/qmd" in missing_qmd_status.stdout
      and "Node.js 22+" in missing_qmd_status.stdout,
      "qmd status did not provide an actionable, non-blocking repair path:\n"
      + missing_qmd_status.stdout + missing_qmd_status.stderr)
missing_qmd = run("qmd", "enable", store=qmd_store)
check(missing_qmd.returncode == 1 and "Could not enable QMD" in
      missing_qmd.stderr and not os.path.exists(projection),
      "failed QMD enable created files or changed memory")
check(open(os.path.join(qmd_store, "LOG.txt"), "rb").read() == restored_log,
      "QMD rebuild, disable, or purge changed authoritative LOG.txt")
cli._qmd_run = real_qmd_run

# The real subprocess boundary always uses a named index, no shell, JSON-safe
# UTF-8, no color, and a finite timeout.
captured_qmd = {}
real_subprocess_run, real_which = cli.subprocess.run, cli.shutil.which


class FakeQmdProcess:
    returncode, stdout, stderr = 0, "[]", ""


def capture_qmd_process(command, **kwargs):
    captured_qmd["command"], captured_qmd["kwargs"] = command, kwargs
    return FakeQmdProcess()


try:
    cli.subprocess.run = capture_qmd_process
    cli.shutil.which = lambda name: "qmd-test" if name == "qmd" else None
    check(cli._qmd_run(["query", "meaning"], timeout=17) == "[]",
          "QMD subprocess adapter did not return stdout")
finally:
    cli.subprocess.run, cli.shutil.which = real_subprocess_run, real_which
    cli._qmd_run = real_qmd_run
check(captured_qmd.get("command")[:3] ==
      ["qmd-test", "--index", "optmem"]
      and captured_qmd["kwargs"].get("shell") is False
      and captured_qmd["kwargs"].get("timeout") == 17
      and captured_qmd["kwargs"]["env"].get("NO_COLOR") == "1",
      "QMD subprocess boundary is not isolated: %r" % captured_qmd)


# zoom: one tree node, opened into its two halves. The tool only reads;
# the agent is the navigator: it descends from a wake line by halving, and
# may leap to any block id it can name.
def halves(bid):
    r = run("zoom", bid)
    check(r.returncode == 0, "zoom %s failed: %s" % (bid, r.stderr))
    out = []
    for line in r.stdout.splitlines():
        m = re.match(r"#(\d+)(?:-(\d+))? ", line)
        check(bool(m), "zoom printed a line with no id: %r" % line)
        a = int(m.group(1))
        out.append((a, int(m.group(2)) + 1 if m.group(2) else a + 1))
    return out


target, lo, hi, calls = 777, 0, 1024, 0
while hi - lo > 1:
    mid = (lo + hi) // 2
    kids = halves("%d-%d" % (lo, hi - 1))
    check(kids == [(lo, mid), (mid, hi)],
          "zoom %d-%d is not its two halves: %r" % (lo, hi - 1, kids))
    lo, hi = kids[target >= mid]
    calls += 1
check(lo == target and calls == 10,
      "halving 1024 memories took %d calls and landed on #%d" % (calls, lo))
check("memory number %d," % target in run("zoom", "776-777").stdout,
      "the last zoom must print the raw memories themselves")

# Optional depth opens several tree levels in one bounded call; depth 1 is
# still the original two-child behavior.
r = run("zoom", "--depth", "2", "0-3")
deep_lines = [line for line in r.stdout.splitlines() if line.startswith("#")]
check(len(deep_lines) == 4
      and [int(line.split()[0][1:]) for line in deep_lines] == list(range(4)),
      "zoom --depth 2 did not reach four raw memories:\n" + r.stdout)
r = run("zoom", "--depth=6", "0-63")
check(len([line for line in r.stdout.splitlines()
           if line.startswith("#")]) == 64,
      "maximum zoom depth did not produce 64 leaves")
check(run("zoom", "--depth", "0", "0-3").returncode == 1,
      "zoom accepted depth zero")
check(run("zoom", "--depth", "7", "0-127").returncode == 1,
      "zoom accepted an unsafe depth")

# the unbuilt tail is named, the empty future is omitted
r = run("zoom", "1024-2047")  # T is N+1, so the right half has no summary
check("#1536-2047 not compressed yet" in r.stdout,
      "an unbuilt half must say so: " + r.stdout)
r = run("zoom", "%d-%d" % (N, N + 1))  # the newest memory + one not yet made
check(r.stdout.count("\n") == 1 and "#%d " % N in r.stdout,
      "a half beyond the newest memory must be omitted: " + r.stdout)

# zoom answers with the tree's own records, so the id must BE a node
check(run("zoom", "3-9").returncode == 1, "zoom accepted a non-block")
check(run("zoom", "9-3").returncode == 1, "zoom accepted a backwards range")
check(run("zoom").returncode == 1, "zoom with no id must show usage")
r = run("zoom", "1048576-2097151")
check(r.returncode == 1 and "beyond the memory" in r.stderr
      and " wake" in r.stderr, "zoom past the end must name a way back")


def treesize():
    t = os.path.join(d, "TREE")
    return sum(os.path.getsize(os.path.join(t, f)) for f in os.listdir(t))

before, logsize = treesize(), os.path.getsize(os.path.join(d, "LOG.txt"))
r = run("forget", "16-31")
check("16-31" in r.stdout, "forget did not report the block: " + r.stdout + r.stderr)
check(treesize() < before, "forget did not shrink the tree")
check(os.path.getsize(os.path.join(d, "LOG.txt")) == logsize, "forget touched the log")
check(run("wake").returncode == 1, "wake should refuse after a forget")
# a settled block cannot be rewritten. Resubmitting one (two sessions paid
# the same nap) is not an error: say it is settled, write nothing
mid = treesize()
r = run("nap", "0-1", "attempted overwrite")
check(r.returncode == 0 and "already settled" in r.stdout,
      "resubmitting a settled block was not reported as settled: " + r.stderr)
check(treesize() == mid, "resubmitting a settled block wrote something")
# a block that is neither settled nor next (here: a dropped ancestor,
# submitted before its half is rebuilt) is a real mistake
r = run("nap", "0-31", "out of order")
check(r.returncode == 1 and "Wrong block" in r.stderr,
      "an out-of-order block was accepted")
n = 0
while True:
    r = run("nap")
    if "Nothing left to compress" in r.stdout:
        break
    bid = nap_id(r.stdout)
    check(run("nap", bid, "rebuilt after forget").returncode == 0, "rebuild rejected")
    n += 1
check(n > 0, "forget created no work")
check(run("wake").returncode == 0, "wake still refuses after rebuilding")
check(treesize() == before, "tree did not return to its original size")
check(run("forget", "17-32").returncode == 1, "forgetting a non-block should fail")
# a summary that is not built yet is named as such, never left blank
run("forget", "16-31")
z = run("zoom", "0-31").stdout
check("#16-31 not compressed yet" in z, "zoom hid a missing summary: " + z)
while True:
    bid = nap_id(run("nap").stdout)
    if not bid:
        break
    run("nap", bid, "rebuilt after forget")
check(treesize() == before, "tree did not return to its original size")
check(run("forget", "1048576-1048577").returncode == 1, "forgetting a missing block should fail")

# UTF-8: multi-byte characters must not shift record boundaries or dodge limits
run("note", "reunião com João em São Paulo: ação aprovada, coração tranquilo")
run("note", "a plain ascii memory right after the accented one")
r = run("recall", "coração")
check("João" in r.stdout, "recall lost the accented memory: " + r.stdout + r.stderr)
r = run("recall", "plain ascii memory right after")
check("#%d " % (N + 2) in r.stdout, "record after a multi-byte one reads shifted")
r = run("note", "ã" * 150)
check(r.returncode == 1 and "300 bytes" in r.stderr,
      "multi-byte note dodged the byte limit: " + r.stderr)

# note landed -> its blocks are pending; settle before the final wake check
while True:
    r = run("nap")
    if "Nothing left to compress" in r.stdout:
        break
    bid = nap_id(r.stdout)
    run("nap", bid, "settled")
check(run("wake").returncode == 0, "wake refuses at the very end")

# a part is rendered as of T, so a note landing mid-wake cannot shift a
# boundary and silently drop a line
T0 = os.path.getsize(os.path.join(d, "LOG.txt")) // 320
before = run("wake", "1", str(T0))
check(before.returncode == 0, "as-of-T wake failed: " + before.stdout + before.stderr)
run("note", "a note that lands between two wake calls")
check(run("wake", "1", str(T0)).stdout == before.stdout,
      "a note between parts changed an already-rendered part")
check(run("wake", "1", str(T0 + 99)).returncode == 1, "wake accepted a future T")

# ...and the agent pays that note's compressions on the spot, as it is told
# to. The tree then holds MORE blocks than the snapshot needs: a level must
# never count as negative work, or the rest of the wake is refused with an
# impossible number.
while True:
    r = run("nap")
    if "Nothing left to compress" in r.stdout:
        break
    run("nap", nap_id(r.stdout), "settled mid-wake")
r = run("wake", "1", str(T0))
check(r.returncode == 0 and r.stdout == before.stdout,
      "a compression paid mid-wake broke the rest of the wake:\n"
      + r.stdout + r.stderr)
for T in list(range(1, 40)) + [T0 - 1, T0, T0 + 1]:
    check(cli.pending_count(d, T) == len(cli.pending(d, T)),
          "pending_count disagrees with pending at T=%d" % T)

# recall must not hand back more than a harness will carry
r = run("recall", "memory number")
check(len(r.stdout) < CAP_CHARS, "recall returned %d chars" % len(r.stdout))
check("(output cap)" in r.stdout and "Narrow the regex" in r.stdout,
      "recall did not explain its output cap")

# ---- concurrency and crash recovery ----------------------------------

d2 = tempfile.mkdtemp(prefix="optmem-race-")
env2 = dict(os.environ, MEMORY_DIR=d2)
P = 16  # real processes: this is the cross-process lock under test
procs = [subprocess.Popen(memo + ["note", "parallel note %d" % i], env=env2,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
         for i in range(P)]
for p in procs:
    p.wait()
with open(os.path.join(d2, "LOG.txt"), "rb") as f:
    recs = [f.read(320) for _ in range(P)]
ids = [r.decode().split()[0] for r in recs if r.strip()]
check(len(ids) == P, "%d of %d parallel notes survived" % (len(ids), P))
check(len(set(ids)) == P, "parallel notes collided on an id: %s" % sorted(ids))
check(sorted(ids) == sorted("#%d" % i for i in range(P)),
      "parallel note ids are not 0..%d: %s" % (P - 1, sorted(ids)))

# a crash mid-append leaves a partial record; the next append must drop it,
# or every later record is misaligned forever
with open(os.path.join(d2, "LOG.txt"), "ab") as f:
    f.write(b"#99 2026-01-01 a half-written record killed by a power cut")
r = run("note", "the memory right after a torn write", store=d2)
check(r.returncode == 0, "note failed after a torn write: " + r.stderr)
sz = os.path.getsize(os.path.join(d2, "LOG.txt"))
check(sz % 320 == 0, "LOG.txt left misaligned after a torn write: %d" % sz)
check("Saved as #%d" % P in r.stdout, "torn record was counted as a memory")
r = run("recall", "right after a torn write", store=d2)
check("#%d " % P in r.stdout, "the memory after a torn write reads wrong")

# a memory small enough to fit one part must still end with the terminator
# the agent was told to wait for
while True:
    r = run("nap", store=d2)
    if "Nothing left to compress" in r.stdout:
        break
    bid = nap_id(r.stdout)
    run("nap", bid, "settled", store=d2)
r = run("wake", store=d2)
check(r.stdout.rstrip().endswith("You are awake."),
      "a one-part wake never says `You are awake.`:\n" + r.stdout)

# a blank summary record (a corrupt write) is work nap cannot see: wake must
# name the one exit, `forget`, instead of refusing forever
d3 = tempfile.mkdtemp(prefix="optmem-blank-")
for i in range(4):
    run("note", "corrupt store memory %d" % i, store=d3)
for bid, s in (("0-1", "one"), ("2-3", "two"), ("0-3", "all")):
    run("nap", bid, s, store=d3)
with open(os.path.join(d3, "config"), "w") as f:
    f.write("WAKE_LINES = 2\n")
with open(os.path.join(d3, "TREE", "2"), "r+b") as f:
    f.write(b" " * 287 + b"\n")
r = run("wake", store=d3)
check(r.returncode == 1 and "resummarize 0-1" in r.stderr
      and "None" not in r.stdout,
      "a blank summary must point at forget:\n" + r.stdout + r.stderr)

# an unreadable level is a filesystem failure and must surface as one --
# reading it as "not compressed yet" offers work that cannot be done
if os.name != "nt":  # chmod does not remove read access on Windows
    os.chmod(os.path.join(d3, "TREE", "2"), 0)
    r_ = subprocess.run(memo + ["wake"], capture_output=True, text=True,
                        env=dict(os.environ, MEMORY_DIR=d3))
    check(r_.returncode == 1 and "Permission denied" in r_.stderr
          and "not compressed" not in r_.stdout,
          "an unreadable level was read as pending work: "
          + r_.stdout + r_.stderr)
    os.chmod(os.path.join(d3, "TREE", "2"), 0o644)

# an impossible calendar date would poison every later import: the store's
# order check compares against it forever
with open(os.path.join(d3, "bad.txt"), "w") as f:
    f.write("2027-99-99 an impossible date\n")
r = run("import", os.path.join(d3, "bad.txt"), store=d3)
check(r.returncode == 1 and "not a real date" in r.stderr,
      "import accepted an impossible date: " + r.stdout + r.stderr)
shutil.rmtree(d3)

# the same blank-record dead end at the other site: a big block's half
d4 = tempfile.mkdtemp(prefix="optmem-half-")
for i in range(32):
    run("note", "half probe memory %d" % i, store=d4)
while True:
    r = run("nap", store=d4)
    if "Compress memories #0-31 " in r.stdout:
        break
    run("nap", nap_id(r.stdout), "settled", store=d4)
with open(os.path.join(d4, "TREE", "16"), "r+b") as f:
    f.write(b" " * 287 + b"\n")
r = run("nap", store=d4)
check(r.returncode == 1 and "resummarize 0-15" in r.stderr,
      "a blank half summary must point at forget: " + r.stdout + r.stderr)
shutil.rmtree(d4)

# the store is UTF-8 whatever the locale says: without pinning the streams,
# one arrow in a memory made wake crash forever on a latin-1 machine
d5 = tempfile.mkdtemp(prefix="optmem-utf8-")
run("note", "an arrow \u2192 survives any locale", store=d5)
r_ = subprocess.run(memo + ["wake"], capture_output=True,
                    env=dict(os.environ, MEMORY_DIR=d5,
                             PYTHONIOENCODING="latin-1"))
check(r_.returncode == 0 and "\u2192".encode() in r_.stdout,
      "wake must print UTF-8 on a non-UTF-8 locale: "
      + repr(r_.stdout + r_.stderr))

# an import file that is not UTF-8 is refused -- neither a traceback nor,
# worse, silently mis-decoded into mojibake and stored forever
with open(os.path.join(d5, "latin1.txt"), "wb") as f:
    f.write(b"2027-01-01 caf\xe9 in latin-1\n")
r = run("import", os.path.join(d5, "latin1.txt"), store=d5)
check(r.returncode == 1 and "not UTF-8" in r.stderr,
      "a non-UTF-8 import file must be refused: " + r.stdout + r.stderr)

# a summary record holding invalid UTF-8 is the blank-record dead end in
# another coat: it must name forget, not print a traceback
run("note", "utf8 probe second memory", store=d5)
run("nap", "0-1", "both utf8 probes", store=d5)
with open(os.path.join(d5, "TREE", "2"), "r+b") as f:
    f.write(b"\xff\xfe corrupt bytes")
r = run("zoom", "0-3", store=d5)
check(r.returncode == 1 and "resummarize 0-1" in r.stderr,
      "a corrupt summary must point at forget: " + r.stdout + r.stderr)
shutil.rmtree(d5)

# re-running init on a lived-in store must not touch one byte of it: the
# whole setup is idempotent, so it is safe on every provision. `init` only
# ever makedirs(exist_ok), opens LOG.txt for APPEND, and writes `config`
# when absent -- never truncating, never overwriting a tuned config.
def fingerprint(path):
    out = {}
    for root, _, files in os.walk(path):
        for f in files:
            if f == ".lock":
                continue
            p = os.path.join(root, f)
            out[os.path.relpath(p, path)] = open(p, "rb").read()
    return out


# `memo config` is how a size is changed: it writes the file the tool reads
# back, an empty value restores the default, and a wake obeys immediately --
# nothing is recomputed, because a size only selects what gets printed.
r = run("config", "WAKE_LINES=12")
check("12" in r.stdout and "default 208" in r.stdout, "config did not set:\n" + r.stdout)
check(len(run("wake").stdout.splitlines()) <= 14,  # content + frontier + awake
      "wake ignored the new size")
r = run("config", "WAKE_LINES=")
check("default" not in r.stdout, "an empty value did not restore the default")
check(len(run("wake").stdout.splitlines()) > 13, "the default did not come back")
for bad in ("WAKE_LINES=0", "WAKE_LINES=x", "ENTRY_CHARS=999", "NOPE=1",
            "WAKE_LINES", "RAW_MAX=1", "RAW_MAX=257", "LOG_REC=63"):
    check(run("config", bad).returncode == 1, "config accepted %s" % bad)

# Record widths are per-store and PHYSICAL: they unlock longer entries, but
# only an empty store may set them -- position is identity in LOG.txt.
check(run("config", "LOG_REC=640").returncode == 1,
      "a nonempty store changed its physical record width")
wide = tempfile.mkdtemp(prefix="optmem-wide-")
os.makedirs(os.path.join(wide, "TREE"))
open(os.path.join(wide, "LOG.txt"), "wb").close()
check(run("config", "ENTRY_CHARS=560", store=wide).returncode == 1,
      "ENTRY_CHARS exceeded the default record widths")
r = run("config", "LOG_REC=640", "TREE_REC=576", "ENTRY_CHARS=560",
        store=wide)
check(r.returncode == 0 and "640" in r.stdout,
      "an empty store could not widen its records: " + r.stderr)
long_text = "w" * 555
check(run("note", long_text, store=wide).returncode == 0,
      "a widened store refused a 555-byte entry")
check(cli.log_get(wide, 0)[2].endswith(long_text)
      and os.path.getsize(os.path.join(wide, "LOG.txt")) == 640,
      "a widened store did not write its configured record width")
check(long_text in run("show", "0", store=wide).stdout,
      "a widened store cannot read back its long entry")
check(run("config", "LOG_REC=320", store=wide).returncode == 1
      and run("config", "LOG_REC=", store=wide).returncode == 1,
      "a store holding records changed or reset its width")
run("note", "wide second entry", store=wide)
wide_summary = "wide summary " + "s" * 400   # over 280, within 560
r = run("nap", "0-1", wide_summary, store=wide)
check(r.returncode == 0 and "0-1 saved" in r.stdout
      and wide_summary in run("show", "0-1", store=wide).stdout,
      "a widened TREE record refused a long summary: " + r.stdout + r.stderr)

# RAW_MAX bounds how many raw memories one merge prompt shows: above it,
# a block merges from its two half summaries instead.
narrow = tempfile.mkdtemp(prefix="optmem-narrow-")
os.makedirs(os.path.join(narrow, "TREE"))
open(os.path.join(narrow, "LOG.txt"), "wb").close()
run("config", "RAW_MAX=2", store=narrow)
for i in range(4):
    run("note", "narrow entry %d" % i, store=narrow)
run("nap", "0-1", "first narrow half", store=narrow)
prompt = run("nap", "2-3", "second narrow half", store=narrow).stdout
check("#0-1 first narrow half" in prompt
      and "#2-3 second narrow half" in prompt
      and "#0 " not in prompt,
      "RAW_MAX=2 still merged a 4-block from raw entries:\n" + prompt)

# RAW_MIN is the floor of the merge tree: the smallest block it builds. It is
# the knob for how OFTEN a compression is asked -- levels start there instead
# of at 2, so N memories owe ~2N/RAW_MIN merges instead of ~N -- and the first
# summary of any range is written from RAW_MIN raw entries, never from a pair.
def complete_floor(T, floor):
    """complete(), but for a tree whose finest level is `floor`."""
    out, size = [], floor
    while size <= T:
        out += [(i * size, (i + 1) * size) for i in range(T // size)]
        size *= 2
    return out


for floor in (2, 8, 64):
    cli.RAW_MIN = floor
    for T in list(range(1, 200)) + [1000, 4096, 10000, 65536]:
        c = cover(T, WAKE_LINES)
        check(len(c) <= WAKE_LINES,
              "RAW_MIN=%d T=%d: %d lines > budget" % (floor, T, len(c)))
        check(c and c[0][0] == 0 and c[-1][1] == T,
              "RAW_MIN=%d T=%d: does not span [0,T)" % (floor, T))
        for a, b in zip(c, c[1:]):
            check(a[1] == b[0], "RAW_MIN=%d T=%d: gap or overlap at %s %s"
                  % (floor, T, a, b))
            check(b[1] - b[0] <= a[1] - a[0],
                  "RAW_MIN=%d T=%d: detail does not increase toward the "
                  "present" % (floor, T))
        for lo, hi in c:
            s = hi - lo
            check(s & (s - 1) == 0 and lo % s == 0,
                  "RAW_MIN=%d T=%d: [%d,%d) is not an aligned block"
                  % (floor, T, lo, hi))
            # the whole point: a cover may ask for a raw memory or for a real
            # summary, never for a summary the tree does not build
            check(s == 1 or s >= floor,
                  "RAW_MIN=%d T=%d: cover wants unbuildable block [%d,%d)"
                  % (floor, T, lo, hi))
        seen_blocks = set(b for b in c if b[1] - b[0] > 1)
        check(seen_blocks <= set(complete_floor(T, floor)),
              "RAW_MIN=%d T=%d: cover wants a block the tree never yields"
              % (floor, T))
cli.RAW_MIN = DEFAULTS["RAW_MIN"]

# The tool's pending() reads level lengths instead of scanning, so it is
# checked against the definition at every floor.
for floor in (2, 8, 32):
    p = tempfile.mkdtemp(prefix="optmem-floor-")
    os.makedirs(os.path.join(p, "TREE"))
    open(os.path.join(p, "LOG.txt"), "wb").close()
    run("config", "RAW_MIN=%d" % floor, "RAW_MAX=%d" % max(16, floor),
        store=p)
    merges = 0
    for i in range(64):
        out = run("note", "floor entry %d" % i, store=p).stdout
        while nap_id(out):
            block = nap_id(out)
            out = run("nap", block, "summary of %s" % block, store=p).stdout
            merges += 1
        cli.RAW_MIN = floor          # pending() below is called in-process
        check(cli.pending(p, i + 1) == [],
              "RAW_MIN=%d: work was left pending after #%d" % (floor, i))
        cli.RAW_MIN = DEFAULTS["RAW_MIN"]
    check(merges == len(complete_floor(64, floor)),
          "RAW_MIN=%d: %d merges for 64 memories, expected %d"
          % (floor, merges, len(complete_floor(64, floor))))
    check(run("wake", store=p).stdout.rstrip().endswith("You are awake."),
          "RAW_MIN=%d: wake broke" % floor)
    shutil.rmtree(p)

# 64 memories: 63 merges at the default floor, 15 at 8, 3 at 32. Raising the
# floor is the only way to be asked less often.
check(len(complete_floor(64, 2)) == 63 and len(complete_floor(64, 8)) == 15
      and len(complete_floor(64, 32)) == 3,
      "the merge count no longer falls as RAW_MIN rises")

floored = tempfile.mkdtemp(prefix="optmem-raw-min-")
os.makedirs(os.path.join(floored, "TREE"))
open(os.path.join(floored, "LOG.txt"), "wb").close()
run("config", "RAW_MIN=8", store=floored)
quiet = []
for i in range(8):
    out = run("note", "floored entry %d" % i, store=floored).stdout
    quiet.append(nap_id(out))
check(quiet[:7] == [None] * 7,
      "RAW_MIN=8 asked a compression before 8 memories existed: %s" % quiet)
check(quiet[7] == "0-7", "RAW_MIN=8 did not ask for 0-7 first: %s" % quiet)
prompt = run("wake", store=floored).stdout
check("nap 0-7" in prompt and all("floored entry %d" % i in prompt
                                  for i in range(8)),
      "the first merge at RAW_MIN=8 was not written from raw entries:\n"
      + prompt)
# below the floor there is no block to name, and the error has to say so with
# an id that exists
r = run("nap", "0-1", "too fine", store=floored)
check(r.returncode == 1 and "8-15" in r.stderr,
      "RAW_MIN=8 accepted or misdescribed a 2-wide block: " + r.stderr)
run("nap", "0-7", "the first eight", store=floored)
for i in range(8, 16):
    out = run("note", "floored entry %d" % i, store=floored).stdout
check(nap_id(out) == "8-15", "the second 8-block was not asked: " + out)
run("nap", "8-15", "the second eight", store=floored)
r = run("zoom", "0-15", store=floored)
check("#0-7 the first eight" in r.stdout and "#8-15 the second eight" in r.stdout,
      "zoom did not open a 16-block into its two halves:\n" + r.stdout)
r = run("zoom", "0-15", "--depth", "2", store=floored)
check("#0 " in r.stdout and "#15 " in r.stdout and "0-7" not in r.stdout,
      "one zoom past the floor did not open every raw memory:\n" + r.stdout)
r = run("doctor", store=floored)
check(r.returncode == 0, "a RAW_MIN=8 store failed doctor:\n" + r.stdout)

# Raising the floor prunes the levels below it. They are a cache: the log is
# untouched, the coarser summaries built from them stay valid, and a read
# never reaches them again.
run("config", "RAW_MIN=2", store=floored)
for i in range(16, 18):
    out = run("note", "floored entry %d" % i, store=floored).stdout
    while nap_id(out):
        block = nap_id(out)
        out = run("nap", block, "fine %s" % block, store=floored).stdout
check(os.path.exists(os.path.join(floored, "TREE", "2")),
      "lowering RAW_MIN did not build the finer level")
before_log = open(os.path.join(floored, "LOG.txt"), "rb").read()
coarse = open(os.path.join(floored, "TREE", "16"), "rb").read()
r = run("config", "RAW_MIN=8", store=floored)
check(r.returncode == 0 and "Pruned TREE/2" in r.stdout
      and "Pruned TREE/4" in r.stdout,
      "raising RAW_MIN did not prune the orphaned levels:\n" + r.stdout)
check(not os.path.exists(os.path.join(floored, "TREE", "2"))
      and not os.path.exists(os.path.join(floored, "TREE", "4"))
      and open(os.path.join(floored, "TREE", "16"), "rb").read() == coarse
      and open(os.path.join(floored, "LOG.txt"), "rb").read() == before_log,
      "pruning touched the log or a level it must keep")
check(run("wake", store=floored).stdout.rstrip().endswith("You are awake."),
      "wake broke after the levels below RAW_MIN were pruned")
r = run("doctor", store=floored)
check(r.returncode == 0, "doctor failed after pruning:\n" + r.stdout)

# Every command that names a block has to agree on where the floor is: below
# RAW_MIN there is no block to name, at it there is exactly one, and nothing
# may offer an id it would then refuse.
edge = tempfile.mkdtemp(prefix="optmem-edge-")
os.makedirs(os.path.join(edge, "TREE"))
open(os.path.join(edge, "LOG.txt"), "wb").close()
run("config", "RAW_MIN=8", "RAW_MAX=8", store=edge)
for i in range(32):
    run("note", "edge entry %d" % i, store=edge)

# --batch draws its run from the floor level, and applying it is atomic
batch = run("nap", "--batch", "4", store=edge)
check(batch.returncode == 0
      and all(("%d-%d:" % pair) in batch.stdout
              for pair in ((0, 7), (8, 15), (16, 23), (24, 31)))
      and "0-1:" not in batch.stdout,
      "nap --batch at RAW_MIN=8 did not draw floor-level jobs:\n"
      + batch.stdout)
check(all("edge entry %d" % i in batch.stdout for i in range(32)),
      "a floor-level batch was not written from raw entries:\n" + batch.stdout)
edge_batch = os.path.join(edge, "batch.tsv")
with open(edge_batch, "w", encoding="utf-8") as f:
    for lo in (0, 8, 16, 24):
        f.write("%d-%d\tedge summary %d\n" % (lo, lo + 7, lo))
r = run("nap", "--apply", edge_batch, store=edge)
check(r.returncode == 0
      and cli.count(cli.tree_path(edge, 8), cli.TREE_REC) == 4
      and not os.path.exists(cli.tree_path(edge, 2))
      and not os.path.exists(cli.tree_path(edge, 4)),
      "applying a floor batch built levels below RAW_MIN:\n" + r.stdout + r.stderr)
# a batch below the floor is not a batch this tree has
stale_edge = os.path.join(edge, "too-fine.tsv")
with open(stale_edge, "w", encoding="utf-8") as f:
    f.write("0-1\ttoo fine to be a block\n")
check(run("nap", "--apply", stale_edge, store=edge).returncode == 1,
      "nap --apply accepted a block below RAW_MIN")

# nap: a floor block reads raw, the level above it reads the two summaries
prompt = run("nap", "0-15", "the first sixteen", store=edge).stdout
r = run("show", "0-15", store=edge)
check("the first sixteen" in r.stdout,
      "a block above the floor did not save:\n" + r.stdout + r.stderr)
for bad in ("0-1", "0-3", "1-8", "8-8"):
    check(run("nap", bad, "no such block", store=edge).returncode == 1,
          "nap accepted %s below or off the RAW_MIN=8 floor" % bad)

# every id any command offers must be one every command accepts
for out in (run("wake", store=edge).stdout,
            run("nap", "--batch", "2", store=edge).stdout):
    for lo, hi in re.findall(r"#(\d+)-(\d+)", out):
        n = int(hi) - int(lo) + 1
        check(n >= 8 and not n & (n - 1) and int(lo) % n == 0,
              "an id below the RAW_MIN=8 floor was offered: #%s-%s" % (lo, hi))

# zoom never bottoms out in "not compressed yet": past the floor it opens raw
r = run("zoom", "0-31", "--depth", "3", store=edge)
check("not compressed yet" not in r.stdout
      and all("edge entry %d" % i in r.stdout for i in range(32)),
      "zoom past the RAW_MIN floor did not reach raw memories:\n" + r.stdout)
r = run("zoom", "0-7", store=edge)
check(all("edge entry %d" % i in r.stdout for i in range(8))
      and "edge summary 0" not in r.stdout,
      "zoom of a floor block did not open its raw memories:\n" + r.stdout)
check(run("zoom", "0-1", store=edge).returncode == 1,
      "zoom accepted a range below the RAW_MIN floor")

# amend and retract of a block: the floor is a block, below it is not, and the
# refusal has to point at zoom rather than leave the agent guessing
r = run("amend", "0-7", "the first eight, corrected", store=edge)
check(r.returncode == 0 and "#0-#7" in r.stdout,
      "amending a floor block failed:\n" + r.stdout + r.stderr)
check("the first eight, corrected" in run("show", "0-7", store=edge).stdout,
      "an amended floor block does not show its replacement")
r = run("retract", "8-15", "superseded by the rewrite", store=edge)
check(r.returncode == 0, "retracting a floor block failed:\n" + r.stderr)
for bad in ("0-1", "2-3", "0-3"):
    check(run("amend", bad, "too fine", store=edge).returncode == 1
          and run("retract", bad, "too fine", store=edge).returncode == 1,
          "amend/retract accepted %s below the RAW_MIN=8 floor" % bad)
# a single memory is still amendable: RAW_MIN is a floor on BLOCKS, not on ids
check(run("amend", "3", "edge entry 3, corrected", store=edge).returncode == 0,
      "RAW_MIN blocked amending one raw memory")

# resummarize drops a floor block and everything above it; the next nap asks
# for exactly that block again, from raw
r = run("resummarize", "0-7", store=edge)
check(r.returncode == 0 and "0-7" in r.stdout,
      "resummarize refused a floor block:\n" + r.stdout + r.stderr)
r = run("wake", store=edge)
check(nap_id(r.stdout) == "0-7",
      "the dropped floor block was not asked for again:\n" + r.stdout)
check(run("resummarize", "0-3", store=edge).returncode == 1,
      "resummarize accepted a range below the floor")
check(run("doctor", store=edge).returncode == 0,
      "a RAW_MIN=8 store failed doctor after amend/retract/resummarize")
shutil.rmtree(edge)

# RAW_MIN == RAW_MAX is the tightest legal window: the floor block still reads
# raw, and the level above it must fall back to the two half summaries.
tight = tempfile.mkdtemp(prefix="optmem-tight-")
os.makedirs(os.path.join(tight, "TREE"))
open(os.path.join(tight, "LOG.txt"), "wb").close()
run("config", "RAW_MIN=4", "RAW_MAX=4", store=tight)
for i in range(8):
    run("note", "tight entry %d" % i, store=tight)
run("nap", "0-3", "first tight half", store=tight)
prompt = run("nap", "4-7", "second tight half", store=tight).stdout
check("#0-3 first tight half" in prompt and "#4-7 second tight half" in prompt
      and "tight entry 0" not in prompt,
      "RAW_MIN=RAW_MAX=4 did not merge 0-7 from its halves:\n" + prompt)
check(run("doctor", store=tight).returncode == 0, "a tight-window store failed doctor")
shutil.rmtree(tight)

# RAW_MIN and RAW_MAX bracket one window: the smallest block the tree builds
# has to fit in a single merge prompt.
for bad in ("RAW_MIN=1", "RAW_MIN=3", "RAW_MIN=512", "RAW_MIN=x"):
    check(run("config", bad, store=floored).returncode == 1,
          "config accepted %s" % bad)
check(run("config", "RAW_MIN=32", store=floored).returncode == 1,
      "RAW_MIN was allowed past the default RAW_MAX")
check(run("config", "RAW_MIN=32", "RAW_MAX=32", store=floored).returncode == 0,
      "RAW_MIN=32 was refused alongside a RAW_MAX that fits it")
with open(os.path.join(floored, "config"), "a", encoding="utf-8") as f:
    f.write("RAW_MIN=64\nRAW_MAX=16\n")
check(run("wake", store=floored).returncode == 1,
      "a config file with RAW_MIN past RAW_MAX was read anyway")
shutil.rmtree(floored)

with open(os.path.join(d, "config"), "a") as f:
    f.write("WAKE_LINES=120\n")          # a size the user tuned by hand
before = fingerprint(d)
check(len(before) > 3 and before["LOG.txt"], "the store under test is empty")
for _ in range(3):
    r = run("init")
    check(r.returncode == 0 and "Found" in r.stdout, "init on a live store failed")
check(fingerprint(d) == before, "init modified an existing memory")
r = run("wake")
check(r.stdout.rstrip().endswith("You are awake."), "wake broke after re-init")

# ---- scope: which memory a command speaks to, when nothing points at one
# Every test above pins MEMORY_DIR, so it also proves MEMORY_DIR still wins.
xdg = tempfile.mkdtemp(prefix="optmem-xdg-")
os.environ["XDG_DATA_HOME"] = xdg
os.environ.pop("MEMORY_DIR", None)
real_git = cli.git


def fake_scope_git(remotes=None, checkout="", upstream=""):
    """A deterministic Git view for scope-selection unit tests."""
    remotes = remotes or {}

    def fake_git(*args):
        if args == ("remote",):
            return "\n".join(sorted(remotes))
        if args[:2] == ("remote", "get-url") and len(args) == 3:
            return remotes.get(args[2], "")
        if args[:2] == ("rev-parse", "--show-toplevel"):
            return checkout
        if args == ("rev-parse", "--abbrev-ref", "--symbolic-full-name",
                    "@{upstream}"):
            return upstream
        return ""
    return fake_git


def as_scope(remotes=None, checkout="", upstream=""):
    cli.git = fake_scope_git(remotes, checkout, upstream)
    try:
        return cli.project_scope()
    finally:
        cli.git = real_git


ssh = as_scope({"origin": "git@github.com:Texarkanine/OptMem.git"})
check("repo-v2" in ssh["store"] and ssh["project"] ==
      "github.com/Texarkanine/OptMem",
      "an ssh remote did not become a full hosted identity: " + ssh["store"])
check(as_scope({"origin": "https://github.com/Texarkanine/OptMem"})["store"]
      == ssh["store"],
      "one repo split across two remote spellings")
check(as_scope({"origin": "git@github.com:Texarkanine/OptMem.git/"})["store"]
      == ssh["store"],
      "a trailing slash forked the memory of one repo")
check(as_scope({
          "origin": "https://token@github.com/Texarkanine/OptMem.git?secret=yes"
      })["store"] == ssh["store"],
      "a credentialed remote leaked into or split project scope")
other_host = as_scope({
    "origin": "git@github-texarkanine.com:Texarkanine/OptMem.git"})
check(other_host["store"] != ssh["store"],
      "different Git hosts still collide in one project scope")
check(cli.canonical_origin("git@github.com:acme/api.git") ==
      "github.com/acme/api"
      and cli.canonical_origin("https://github.com/acme/api.git") ==
      "github.com/acme/api"
      and cli.canonical_origin("https://gitlab.com/acme/api.git") ==
      "gitlab.com/acme/api"
      and cli.canonical_origin(
          "https://gitlab.com/acme/platform/services/api.git") ==
      "gitlab.com/acme/platform/services/api",
      "host-aware origin canonicalization is unstable")
check(cli.safe_origin_alias(
          "https://secret-token@github.com/acme/api.git?credential=hidden") ==
      "https://github.com/acme/api.git",
      "scope identity alias retained URL credentials")

deep_a = as_scope({
    "origin": "https://gitlab.com/acme/platform/services/api.git"})
deep_b = as_scope({
    "origin": "https://gitlab.com/other/platform/services/api.git"})
check(deep_a["store"] != deep_b["store"]
      and deep_a["project"] == "gitlab.com/acme/platform/services/api",
      "deep hosted namespaces still collapse to their final owner/repo")

sole = as_scope({"upstream": "git@github.com:acme/sole.git"})
check(sole["remote_name"] == "upstream"
      and "only usable Git remote" in sole["source"],
      "a sole non-origin remote was not selected automatically")
tracked = as_scope({
    "fork": "git@github.com:user/tool.git",
    "upstream": "git@github.com:acme/tool.git",
}, upstream="upstream/main")
check(tracked["remote_name"] == "upstream"
      and tracked["project"] == "github.com/acme/tool",
      "the tracked remote did not resolve an origin-less checkout")
ambiguous_root = tempfile.mkdtemp(prefix="optmem-ambiguous-")
ambiguous = as_scope({
    "fork": "git@github.com:user/tool.git",
    "upstream": "git@github.com:acme/tool.git",
}, checkout=ambiguous_root)
check(ambiguous["kind"] == "path"
      and "ambiguous remotes" in ambiguous["source"],
      "ambiguous remotes were guessed instead of using the checkout")
os.environ["OPTMEM_REMOTE"] = "fork"
try:
    explicit = as_scope({
        "fork": "git@github.com:user/tool.git",
        "upstream": "git@github.com:acme/tool.git",
    }, checkout=ambiguous_root)
finally:
    os.environ.pop("OPTMEM_REMOTE")
check(explicit["remote_name"] == "fork"
      and explicit["project"] == "github.com/user/tool"
      and explicit["source"] == "OPTMEM_REMOTE",
      "OPTMEM_REMOTE did not resolve an intentionally selected remote")

# New path scopes follow a moved directory on the same filesystem without
# leaving marker files in the project.
path_project = tempfile.mkdtemp(prefix="optmem-path-project-")
path_before = as_scope({}, path_project)
check(not os.path.exists(os.path.join(path_project, ".optmem-scope"))
      and not os.path.exists(path_before["store"]),
      "read-only path scope inspection created identity or storage")
cli.git = fake_scope_git({}, path_project)
try:
    path_store = cli.store()
finally:
    cli.git = real_git
check(os.path.isfile(os.path.join(path_project, ".optmem-scope")),
      "first path-memory use did not create its portable identity marker")
path_moved = path_project + "-moved"
os.rename(path_project, path_moved)
path_after = as_scope({}, path_moved)
check(path_store == path_after["store"]
      and path_after["identity"].startswith("marker:")
      and "stable across directory and filesystem moves" in
          path_after["source"],
      "moving a path-scoped project changed its automatic memory scope")

# Concurrent first use must converge on one marker and one store.
race_project = tempfile.mkdtemp(prefix="optmem-path-race-")
race_env = dict(os.environ, XDG_DATA_HOME=xdg)
writers = [
    subprocess.Popen(memo + ["note", "path scope writer %d" % index],
                     cwd=race_project, env=race_env,
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for index in range(2)
]
race_results = [writer.communicate() + (writer.returncode,)
                for writer in writers]
race_scope = as_scope({}, race_project)
check(all(result[2] == 0 for result in race_results)
      and os.path.isfile(os.path.join(race_project, ".optmem-scope"))
      and os.path.getsize(os.path.join(race_scope["store"], "LOG.txt")) ==
          2 * cli.LOG_REC,
      "concurrent path-scope initialization split or lost memory:\n"
      + "\n".join(stdout + stderr
                  for stdout, stderr, _ in race_results))

invalid_marker_project = tempfile.mkdtemp(prefix="optmem-invalid-marker-")
with open(os.path.join(invalid_marker_project, ".optmem-scope"), "w") as f:
    f.write("not an OptMem marker\n")
invalid_scope = subprocess.run(
    memo + ["scope"], cwd=invalid_marker_project, env=race_env,
    capture_output=True, text=True)
invalid_note = subprocess.run(
    memo + ["note", "must not enter an ambiguous scope"],
    cwd=invalid_marker_project, env=race_env, capture_output=True, text=True)
check(invalid_scope.returncode == 0 and "Warning:" in invalid_scope.stdout
      and "invalid or unsafe" in invalid_scope.stdout
      and invalid_note.returncode == 1
      and "Path-scope marker is invalid or unsafe" in invalid_note.stderr
      and "Traceback" not in invalid_note.stderr,
      "an invalid path marker was hidden or silently replaced:\n"
      + invalid_scope.stdout + invalid_scope.stderr
      + invalid_note.stdout + invalid_note.stderr)

# Existing path-based stores gain a central identity link on first use, so the
# same move compatibility applies without moving or rewriting the store.
legacy_project = tempfile.mkdtemp(prefix="optmem-legacy-project-")
legacy_store = cli._legacy_path_dir(cli._scope_root(), legacy_project)
os.makedirs(os.path.join(legacy_store, "TREE"))
open(os.path.join(legacy_store, "LOG.txt"), "a").close()
cli.git = fake_scope_git({}, legacy_project)
try:
    check(cli.project_scope()["store"] == legacy_store,
          "an existing legacy path store was not preserved")
    check(cli.store() == legacy_store,
          "using an existing legacy path store selected another store")
finally:
    cli.git = real_git
legacy_moved = legacy_project + "-moved"
os.rename(legacy_project, legacy_moved)
linked = as_scope({}, legacy_moved)
check(linked["store"] == legacy_store and linked["layout"] == "linked path",
      "a moved legacy path store was not recovered through scope-map.json")

# A claimed legacy owner/repo store must not be reused by another host.
collision_legacy = os.path.join(
    xdg, "optmem", "repo", "acme", "collision")
os.makedirs(os.path.join(collision_legacy, "TREE"))
open(os.path.join(collision_legacy, "LOG.txt"), "a").close()
with open(os.path.join(collision_legacy, "scope.json"), "w",
          encoding="utf-8") as f:
    json.dump({"version": 1, "canonical": "github.com/acme/collision",
               "aliases": ["git@github.com:acme/collision.git"]}, f)
isolated = as_scope({
    "origin": "https://gitlab.com/acme/collision.git"})
check(isolated["store"] != collision_legacy
      and isolated["project"] == "gitlab.com/acme/collision"
      and "isolated store" in isolated["warning"],
      "a cross-host legacy collision was not isolated")

# Old releases truncated deep namespaces in `canonical`, but retained the full
# remote alias. That alias safely proves ownership and upgrades in place.
deep_legacy = os.path.join(xdg, "optmem", "repo", "services", "api")
os.makedirs(os.path.join(deep_legacy, "TREE"))
open(os.path.join(deep_legacy, "LOG.txt"), "a").close()
deep_url = "https://gitlab.com/acme/platform/services/api.git"
with open(os.path.join(deep_legacy, "scope.json"), "w",
          encoding="utf-8") as f:
    json.dump({"version": 1, "canonical": "gitlab.com/services/api",
               "aliases": [deep_url]}, f)
cli.git = fake_scope_git({"origin": deep_url})
try:
    check(cli.project_scope()["store"] == deep_legacy,
          "a matching deep-namespace legacy store was abandoned")
    check(cli.store() == deep_legacy,
          "a matching legacy store was not used during identity upgrade")
finally:
    cli.git = real_git
with open(os.path.join(deep_legacy, "scope.json"), encoding="utf-8") as f:
    upgraded_identity = json.load(f)
check(upgraded_identity["canonical"] ==
      "gitlab.com/acme/platform/services/api",
      "a safely matched legacy identity was not upgraded to its full namespace")

# --global reaches the one memory that is not a project's, and nothing else.
cli.SCOPE_GLOBAL = True
check(cli.memory_dir() == os.path.expanduser(cli.GLOBAL), "--global missed")
os.environ["MEMORY_DIR"] = d
check(cli.memory_dir() == d, "MEMORY_DIR no longer wins over --global")
os.environ.pop("MEMORY_DIR")
cli.SCOPE_GLOBAL = False

# end to end: a real checkout remembers into its own memory, not the global.
repo = tempfile.mkdtemp(prefix="optmem-repo-")
subprocess.run(["git", "init", "-q", repo], check=True)
subprocess.run(["git", "-C", repo, "remote", "add", "origin",
                "git@github.com:acme/widget.git"], check=True)
bad_remote = subprocess.run(
    memo + ["scope"], cwd=repo, capture_output=True, text=True,
    env=dict(os.environ, XDG_DATA_HOME=xdg, OPTMEM_REMOTE="missing"))
check(bad_remote.returncode == 1
      and "OPTMEM_REMOTE names 'missing'" in bad_remote.stderr
      and "origin" in bad_remote.stderr
      and "Traceback" not in bad_remote.stderr,
      "an invalid explicit remote did not fail actionably:\n"
      + bad_remote.stdout + bad_remote.stderr)
e2e = subprocess.run(memo + ["note", "scoped memories land in the project"],
                     cwd=repo, capture_output=True, text=True,
                     env=dict(os.environ, XDG_DATA_HOME=xdg))
check(e2e.returncode == 0 and "Saved as #0." in e2e.stdout,
      "a fresh checkout could not record its first memory: "
      + e2e.stdout + e2e.stderr)
identity = "github.com/acme/widget"
project_store = os.path.join(
    xdg, "optmem", "repo-v2",
    "widget-" + cli._scope_key(identity))
log = os.path.join(project_store, "LOG.txt")
check(os.path.exists(log), "the project memory was not created at " + log)
scope_file = os.path.join(os.path.dirname(log), "scope.json")
with open(scope_file, encoding="utf-8") as f:
    scope_identity = json.load(f)
check(scope_identity["canonical"] == "github.com/acme/widget"
      and "git@github.com:acme/widget.git" in scope_identity["aliases"],
      "project store did not record its host-aware identity")
r = subprocess.run(memo + ["recall", "scoped"], cwd=repo, capture_output=True,
                   text=True, env=dict(os.environ, XDG_DATA_HOME=xdg))
check("1 match" in r.stdout or "scoped memories" in r.stdout,
      "recall did not read the project memory: " + r.stdout + r.stderr)
subprocess.run(["git", "-C", repo, "remote", "set-url", "origin",
                "https://gitlab.com/acme/widget.git"], check=True)
separate = subprocess.run(memo + ["scope"], cwd=repo, capture_output=True,
                          text=True, env=dict(os.environ, XDG_DATA_HOME=xdg))
check(separate.returncode == 0
      and "Project: gitlab.com/acme/widget" in separate.stdout
      and project_store not in separate.stdout
      and "[not created]" in separate.stdout,
      "a second host did not receive a separate automatic scope:\n"
      + separate.stdout + separate.stderr)
subprocess.run(["git", "-C", repo, "remote", "set-url", "origin",
                "git@github.com:acme/widget.git"], check=True)
remembered = subprocess.run(memo + ["recall", "scoped"], cwd=repo,
                            capture_output=True, text=True,
                            env=dict(os.environ, XDG_DATA_HOME=xdg))
check("scoped memories" in remembered.stdout,
      "returning to the original host did not recover its memory:\n"
      + remembered.stdout + remembered.stderr)

shutil.rmtree(repo)
shutil.rmtree(ambiguous_root)
shutil.rmtree(path_moved)
shutil.rmtree(race_project)
shutil.rmtree(invalid_marker_project)
shutil.rmtree(legacy_moved)
shutil.rmtree(fake_release)
shutil.rmtree(maintenance_home)
shutil.rmtree(setup_dir)
shutil.rmtree(fresh["HOME"])
shutil.rmtree(xdg)
shutil.rmtree(d2)
shutil.rmtree(qmd_store)
shutil.rmtree(d)
print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
