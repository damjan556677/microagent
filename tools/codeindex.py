"""Code-index tool: build cscope.out, ctags `tags`, and compile_commands.json for the
active kernel tree so cscope/ctags/clangd can navigate it.

Uses the kernel's own Kbuild targets (`make ARCH=arm64 cscope|tags|compile_commands.json`)
which are arch-aware and reuse existing .o.cmd files — no `bear` needed for the compile DB,
though `bear` is used as a fallback for non-Kbuild trees.
"""
import os
import shutil
import subprocess
import time

from .spec import schema, truncate


# clangd uses clang, which rejects several GCC-only flags Kbuild passes (notably
# -mabi=lp64 -> CreateTargetInfo() returns null -> no AST). A .clangd config strips them
# so clangd can build the preamble. Written into the tree by build_index if absent.
_CLANGD_CONFIG = """\
CompileFlags:
  Remove:
    - -mabi=lp64
    - -mstack-protector-guard*
    - -mtraceback=*
    - -mno-fp-ret-in-387
    - -mskip-rax-setup
    - -fno-allow-store-data-races
    - -fconserve-stack
    - -fno-var-tracking-assignments
  Add:
    - -Wno-unknown-warning-option
    - -Wno-unknown-attributes
    - -ferror-limit=0
"""


def _ensure_clangd_config(root) -> str:
    path = os.path.join(root, ".clangd")
    if os.path.exists(path):
        return ".clangd: present (left as-is)"
    try:
        with open(path, "w") as f:
            f.write(_CLANGD_CONFIG)
        return ".clangd: written (strips GCC-only flags so clang can parse)"
    except Exception as e:
        return f".clangd: could not write ({e})"


def _make(ctx, target, timeout):
    cfg = ctx.cfg
    cmd = ["make", f"ARCH={cfg.arch}", f"CROSS_COMPILE={cfg.cross_compile}", target]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=cfg.active_dir, capture_output=True, text=True,
                           timeout=timeout, errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or ""), time.time() - t0
    except subprocess.TimeoutExpired:
        return 124, f"(timed out after {timeout}s)", time.time() - t0
    except Exception as e:
        return 1, f"(error: {e})", time.time() - t0


def build_index(ctx, what: str = "compile_commands", timeout: int = 1200) -> str:
    """Build navigation indexes for the active tree. what: compile_commands (default, fast — what
    clangd needs) | cscope | tags | all. cscope/tags scan the whole kernel and are SLOW."""
    cfg = ctx.cfg
    root = cfg.active_dir
    if not os.path.isdir(root):
        return f"(error: working directory not found: {root})"
    if not os.path.exists(os.path.join(root, "Makefile")):
        return (f"(error: no top-level Makefile at {root} — not a Kbuild tree, so build_index can't "
                f"run `make compile_commands.json`/cscope/tags. Fall back to `search` for navigation. "
                f"To enable clangd, generate compile_commands.json out-of-band (e.g. "
                f"`cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` or `bear -- <build>`), then retry.)")
    want = ["cscope", "tags", "compile_commands"] if what == "all" else [what]
    report = []
    if "compile_commands" in want:
        report.append(_ensure_clangd_config(root))
    for w in want:
        if w == "compile_commands":
            rc, out, dt = _make(ctx, "compile_commands.json", timeout)
            ok = rc == 0 and os.path.exists(os.path.join(root, "compile_commands.json"))
            if not ok and shutil.which("bear"):     # fallback for non-Kbuild layouts
                try:
                    p = subprocess.run(
                        ["bear", "--", "make", f"ARCH={cfg.arch}",
                         f"CROSS_COMPILE={cfg.cross_compile}", "Image"],
                        cwd=root, capture_output=True, text=True, timeout=timeout, errors="replace")
                    ok = p.returncode == 0 and os.path.exists(os.path.join(root, "compile_commands.json"))
                    out = (p.stdout or "") + (p.stderr or "")
                except Exception as e:
                    out = f"(bear fallback failed: {e})"
            if ok:                              # new CDB — drop any stale cached clangd client
                from . import nav
                nav.invalidate_clangd(ctx)
            report.append(f"compile_commands.json: {'OK' if ok else 'FAILED'} ({dt:.0f}s)"
                          + ("" if ok else "\n  " + out.strip()[-300:]))
        elif w == "cscope":
            rc, out, dt = _make(ctx, "cscope", timeout)
            ok = os.path.exists(os.path.join(root, "cscope.out"))
            report.append(f"cscope.out: {'OK' if ok else 'FAILED'} ({dt:.0f}s)"
                          + ("" if ok else "\n  " + out.strip()[-300:]))
        elif w == "tags":
            rc, out, dt = _make(ctx, "tags", timeout)
            ok = os.path.exists(os.path.join(root, "tags"))
            report.append(f"tags: {'OK' if ok else 'FAILED'} ({dt:.0f}s)"
                          + ("" if ok else "\n  " + out.strip()[-300:]))
        else:
            report.append(f"(unknown index kind {w!r})")
    return truncate(f"index ({root}):\n" + "\n".join(report))


TOOLS = [
    ("build_index", build_index, schema(
        "build_index",
        "Build navigation indexes for the active kernel tree. Default 'compile_commands' (fast — "
        "what the clangd nav tools need; also writes a .clangd). 'cscope'/'tags'/'all' scan the "
        "whole kernel and are SLOW (minutes) — only build them if you specifically need cscope/ctags.",
        {"what": {"type": "string", "enum": ["compile_commands", "cscope", "tags", "all"],
                  "description": "which index to build (default compile_commands).",
                  "default": "compile_commands"}})),
]
