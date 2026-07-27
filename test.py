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
DEFAULTS = {k: getattr(cli, k) for k in
            ("ENTRY_CHARS", "WAKE_LINES", "PART_CHARS", "PART_LINES")}

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
      and "doctor" in helped.stdout and "qmd [help]" in helped.stdout
      and "recall [options]" in helped.stdout,
      "--help is not a useful command overview:\n" + helped.stdout + helped.stderr)
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
    check("upgrade" in completed.stdout and "uninstall" in completed.stdout,
          "%s completion omits maintenance commands" % shell)
    check("limit" in completed.stdout and "context" in completed.stdout
          and "depth" in completed.stdout and "semantic" in completed.stdout
          and "qmd" in completed.stdout and "purge" in completed.stdout,
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
check("append-only log" in init.stdout
      and "binary tree of lossy one-line" in init.stdout
      and "raw memories remain searchable" in init.stdout
      and "max 280 UTF-8 bytes" in init.stdout
      and "recall --semantic" in init.stdout
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

# A paginated global wake must enter the project after its final page. Without
# the private continuation marker, it would say "awake" after global page 2
# and silently skip all project context.
subprocess.run(memo + ["--global", "config", "PART_LINES=1"],
               env=fresh, capture_output=True, check=True)
for text in ("global page one", "global page two"):
    subprocess.run(memo + ["--global", "note", text],
                   env=fresh, capture_output=True, check=True)
subprocess.run(memo + ["note", "project page after global"],
               env=fresh, capture_output=True, check=True)
page1 = subprocess.run(memo + ["wake"], env=fresh,
                       capture_output=True, text=True, check=True)
continuations = [l.split("Run: ", 1)[1] for l in page1.stdout.splitlines()
                 if "Run: " in l and "--then-project" in l]
check(len(continuations) == 1,
      "a paged global wake lost its project continuation:\n" + page1.stdout)
if continuations:
    if os.name == "nt":
        final_page = subprocess.run(
            ["powershell", "-NoProfile", "-Command", continuations[0]],
            env=fresh, capture_output=True, text=True)
    else:
        final_page = subprocess.run(continuations[0], shell=True, env=fresh,
                                    capture_output=True, text=True)
    check(final_page.returncode == 0
          and "project page after global" in final_page.stdout,
          "global pagination never entered the project:\n"
          + final_page.stdout + final_page.stderr)

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
check("You are awake." in run("wake", str(len(parts))).stdout,
      "last part must say it is last")
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
check(first_segment.startswith("#0 2026-07-01 qmd canonical memory 0\n\n")
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
      and "#18 2026-07-05 qmd canonical memory 18" in semantic.stdout
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
check(r.returncode == 1 and "forget 0-1" in r.stderr
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
check(r.returncode == 1 and "forget 0-15" in r.stderr,
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
check(r.returncode == 1 and "forget 0-1" in r.stderr,
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
check(len(run("wake").stdout.splitlines()) <= 13, "wake ignored the new size")
r = run("config", "WAKE_LINES=")
check("default" not in r.stdout, "an empty value did not restore the default")
check(len(run("wake").stdout.splitlines()) > 13, "the default did not come back")
for bad in ("WAKE_LINES=0", "WAKE_LINES=x", "ENTRY_CHARS=999", "NOPE=1", "WAKE_LINES"):
    check(run("config", bad).returncode == 1, "config accepted %s" % bad)

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


def as_repo(url, checkout=""):
    """Resolve a scope with a fake origin and checkout."""
    def fake_git(*args):
        if args[:2] == ("remote", "get-url"):
            return url or ""
        if args[:2] == ("rev-parse", "--show-toplevel"):
            return checkout
        return ""
    cli.git = fake_git
    try:
        return cli.scope_dir()
    finally:
        cli.git = real_git


ssh = as_repo("git@github-texarkanine.com:Texarkanine/OptMem.git")
check(ssh == os.path.join(xdg, "optmem", "repo", "Texarkanine", "OptMem"),
      "an ssh remote did not reduce to owner/repo: " + ssh)
check(as_repo("https://github.com/Texarkanine/OptMem") == ssh,
      "one repo split across two remote spellings")
check(as_repo("git@github.com:Texarkanine/OptMem.git/") == ssh,
      "a trailing slash forked the memory of one repo")
fallback = as_repo(None, os.path.abspath(os.sep + os.path.join("work", "repo")))
check(fallback.startswith(os.path.join(xdg, "optmem", "path")),
      "no remote did not fall back to a portable path: " + fallback)
check(":" not in os.path.relpath(fallback, os.path.join(xdg, "optmem")),
      "the fallback scope contains an invalid Windows drive separator: "
      + fallback)
check(as_repo("", os.path.abspath(os.sep + os.path.join("work", "repo")))
      == fallback, "an empty remote is not the no-remote case")

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
e2e = subprocess.run(memo + ["note", "scoped memories land in the project"],
                     cwd=repo, capture_output=True, text=True,
                     env=dict(os.environ, XDG_DATA_HOME=xdg))
check(e2e.returncode == 0 and "Saved as #0." in e2e.stdout,
      "a fresh checkout could not record its first memory: "
      + e2e.stdout + e2e.stderr)
log = os.path.join(xdg, "optmem", "repo", "acme", "widget", "LOG.txt")
check(os.path.exists(log), "the project memory was not created at " + log)
r = subprocess.run(memo + ["recall", "scoped"], cwd=repo, capture_output=True,
                   text=True, env=dict(os.environ, XDG_DATA_HOME=xdg))
check("1 match" in r.stdout or "scoped memories" in r.stdout,
      "recall did not read the project memory: " + r.stdout + r.stderr)

shutil.rmtree(repo)
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
