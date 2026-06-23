"""Shell execution with a destructive-op safety gate.

The agent is autonomous, but commands matching the configured destructive patterns
(raw-disk flashing: dd/mkfs/parted/wipefs, writes to /dev/sd*,/dev/mmcblk*) always
require explicit confirmation. In one-shot/non-interactive mode (no confirm callback)
such commands are refused rather than run.
"""
import os
import subprocess
import time

from .spec import schema, truncate


def run(ctx, command: str, cwd: str = "", timeout: int = 600) -> str:
    base = ctx.cfg.active_dir
    workdir = (cwd if os.path.isabs(cwd) else os.path.join(base, cwd)) if cwd else base
    if not os.path.isdir(workdir):
        return f"(error: cwd does not exist: {workdir})"

    matched = ctx.cfg.autonomy.is_destructive(command)
    if matched:
        prompt = (f"⚠ Destructive command gated (matched /{matched}/):\n    {command}\n"
                  f"  cwd={workdir}\nAllow this to run?")
        if not ctx.ask(prompt):
            return (f"(blocked: this command matched a destructive pattern (/{matched}/) and was NOT run. "
                    f"It needs explicit human confirmation. If unintended, choose a non-destructive approach.)")

    t0 = time.time()
    try:
        p = subprocess.run(command, shell=True, cwd=workdir, capture_output=True,
                           text=True, timeout=timeout, errors="replace")
        rc, out = p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return f"$ {command}\n(cwd={workdir})\n(error: timed out after {timeout}s)"
    except Exception as e:
        return f"$ {command}\n(cwd={workdir})\n(error: {e})"
    dt = time.time() - t0
    header = f"$ {command}\n(cwd={workdir})\nexit={rc}  time={dt:.1f}s\n"
    return truncate(header + (out if out.strip() else "(no output)"))


TOOLS = [
    ("run", run, schema(
        "run",
        "Run a shell command (bash -c) in the active tree (or `cwd`). Returns exit code + combined "
        "stdout/stderr. Use for builds, git, grep pipelines, etc. Destructive disk commands "
        "(dd/mkfs/parted on /dev/sd*,/dev/mmcblk*) are gated and require confirmation.",
        {"command": {"type": "string", "description": "the shell command line."},
         "cwd": {"type": "string", "description": "working dir relative to the active tree (default the tree root).",
                 "default": ""},
         "timeout": {"type": "integer", "description": "seconds before kill (default 600).", "default": 600}},
        ["command"])),
]
