"""Shell execution with a destructive-op safety gate.

The agent is autonomous, but commands matching the configured destructive patterns
(raw-disk flashing: dd/mkfs/parted/wipefs, writes to /dev/sd*,/dev/mmcblk*) always
require explicit confirmation. In one-shot/non-interactive mode (no confirm callback)
such commands are refused rather than run.
"""
import os
import re
import subprocess
import time

from .spec import schema

_ERR_RE = re.compile(r"(?i)\b(error|fatal|failed|undefined reference|no such file|"
                     r"cannot|not found|segmentation fault|non-zero exit)\b")


def _run_truncate(text: str, n: int = 8000) -> str:
    """Truncate `run` output biased toward the TAIL (build verdicts/errors live there) and surface
    any error/warning lines from the dropped middle, so a failure mid-log isn't silently hidden."""
    if len(text) <= n:
        return text
    keep_head, keep_tail = n // 4, n - n // 4
    head, mid, tail = text[:keep_head], text[keep_head:-keep_tail], text[-keep_tail:]
    errs = [ln for ln in mid.splitlines() if _ERR_RE.search(ln)][:40]
    note = f"\n... [{len(mid)} chars omitted"
    if errs:
        note += "; error/warning lines from the omitted section:\n" + "\n".join(errs)
    note += "] ...\n"
    return head + note + tail


def run(ctx, command: str, cwd: str = "", timeout: int = 1800) -> str:
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
    except subprocess.TimeoutExpired as e:
        def _dec(x):
            return "" if x is None else (x if isinstance(x, str) else x.decode("utf-8", "replace"))
        part = _dec(e.stdout) + _dec(e.stderr)              # keep partial output — don't discard it
        body = (f"$ {command}\n(cwd={workdir})\n(error: timed out after {timeout}s — if this is a "
                f"long build, retry with a larger `timeout`. Partial output below:)\n" + part)
        return _run_truncate(body)
    except Exception as e:
        return f"$ {command}\n(cwd={workdir})\n(error: {e})"
    dt = time.time() - t0
    header = f"$ {command}\n(cwd={workdir})\nexit={rc}  time={dt:.1f}s\n"
    return _run_truncate(header + (out if out.strip() else "(no output)"))


TOOLS = [
    ("run", run, schema(
        "run",
        "Run a shell command (bash -c) in the active tree (or `cwd`). Returns exit code + combined "
        "stdout/stderr (tail-biased if long; error lines from any omitted middle are surfaced). Use "
        "for builds, git, grep pipelines, etc. For long builds, raise `timeout`. Destructive disk "
        "commands (dd/mkfs/parted on /dev/sd*,/dev/mmcblk*) are gated and require confirmation.",
        {"command": {"type": "string", "description": "the shell command line."},
         "cwd": {"type": "string", "description": "working dir relative to the active tree (default the tree root).",
                 "default": ""},
         "timeout": {"type": "integer", "description": "seconds before kill (default 1800; raise for long builds).",
                     "default": 1800}},
        ["command"])),
]
