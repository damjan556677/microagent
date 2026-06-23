"""Text/symbol search: ripgrep (with grep fallback), cscope, ctags.

`rg` is missing on this box, so `search` transparently falls back to `grep -rn`.
cscope/ctags need an index built first (tools/codeindex.build_index, or /index).
"""
import os
import shutil
import subprocess

from .spec import schema, truncate


def _run(cmd, cwd, timeout=60):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "(search timed out)"
    except FileNotFoundError as e:
        return 127, f"(missing binary: {e})"
    except Exception as e:
        return 1, f"(search error: {e})"


def search(ctx, pattern: str, path: str = ".", glob: str = "", max_results: int = 200) -> str:
    """Search file contents. Uses ripgrep if present, else grep -rn."""
    base = ctx.cfg.active_dir
    target = path if os.path.isabs(path) else os.path.join(base, path)
    if shutil.which("rg"):
        cmd = ["rg", "-n", "--no-heading", "--color=never", "-S"]
        if glob:
            cmd += ["-g", glob]
        cmd += ["-e", pattern, target]
        engine = "rg"
    else:
        cmd = ["grep", "-rnI", "--color=never", "--exclude-dir=.git"]
        if glob:
            cmd += [f"--include={glob}"]
        cmd += ["-e", pattern, target]
        engine = "grep"
    rc, out = _run(cmd, base, timeout=120)
    lines = out.splitlines()
    if rc not in (0, 1):                 # 1 == "no matches" for both tools
        return f"(search failed via {engine}: {out.strip()[:300]})"
    if not lines:
        return f"no matches for {pattern!r} (engine: {engine})"
    shown = lines[:max_results]
    extra = "" if len(lines) <= max_results else f"\n... [{len(lines) - max_results} more matches; narrow with glob/path]"
    return truncate(f"{len(lines)} match(es) for {pattern!r} via {engine}:\n" + "\n".join(shown) + extra)


# cscope line-mode query selectors (cscope -L -<n> <pattern>)
_CSCOPE_KIND = {
    "symbol": "0", "definition": "1", "callees": "2", "callers": "3",
    "text": "4", "egrep": "6", "file": "7", "includers": "8",
}


def cscope(ctx, query: str, kind: str = "definition") -> str:
    """Query a cscope database (cscope.out) in the active tree.

    kind: definition | callers | callees | symbol | text | egrep | file | includers
    """
    base = ctx.cfg.active_dir
    if not os.path.exists(os.path.join(base, "cscope.out")):
        return ("(no cscope.out in the active tree — run build_index first, or /index in the REPL.)")
    sel = _CSCOPE_KIND.get(kind)
    if sel is None:
        return f"(error: unknown cscope kind {kind!r}; use one of {sorted(_CSCOPE_KIND)})"
    rc, out = _run(["cscope", "-dL", "-" + sel, query], base, timeout=90)
    out = out.strip()
    if not out:
        return f"no cscope {kind} results for {query!r}"
    rows = []
    for ln in out.splitlines()[:300]:
        parts = ln.split(" ", 3)        # file function line text
        if len(parts) == 4:
            f, fn, lno, txt = parts
            rows.append(f"{f}:{lno}  {fn}\t{txt}")
        else:
            rows.append(ln)
    return truncate(f"cscope {kind} {query!r}:\n" + "\n".join(rows))


def ctags(ctx, symbol: str) -> str:
    """Look up a symbol in a ctags `tags` file via readtags."""
    base = ctx.cfg.active_dir
    if not os.path.exists(os.path.join(base, "tags")):
        return "(no tags file in the active tree — run build_index first, or /index in the REPL.)"
    if shutil.which("readtags"):
        rc, out = _run(["readtags", "-t", "tags", "-e", symbol], base, timeout=60)
    else:
        rc, out = _run(["grep", "-P", f"^{symbol}\\t", "tags"], base, timeout=60)
    out = out.strip()
    return truncate(f"ctags {symbol!r}:\n{out}" if out else f"no tags entry for {symbol!r}")


TOOLS = [
    ("search", search, schema(
        "search",
        "Search file CONTENTS for a regex (ripgrep, or grep fallback). Fast, no index needed. "
        "Scope with `path` and/or a `glob`.",
        {"pattern": {"type": "string", "description": "regex to search for."},
         "path": {"type": "string", "description": "subpath under the active tree (default '.').", "default": "."},
         "glob": {"type": "string", "description": "optional filename glob, e.g. '*.c'.", "default": ""},
         "max_results": {"type": "integer", "default": 200}},
        ["pattern"])),
    ("cscope", cscope, schema(
        "cscope",
        "Query the cscope index of the active tree. kind=definition (where defined), callers (who calls X), "
        "callees (what X calls), symbol (all uses), text, file, includers. Needs build_index first.",
        {"query": {"type": "string", "description": "symbol or text to look up."},
         "kind": {"type": "string",
                  "enum": ["definition", "callers", "callees", "symbol", "text", "egrep", "file", "includers"],
                  "default": "definition"}},
        ["query"])),
    ("ctags", ctags, schema(
        "ctags", "Look up a symbol's declaration(s) in the ctags `tags` index. Needs build_index first.",
        {"symbol": {"type": "string"}},
        ["symbol"])),
]
