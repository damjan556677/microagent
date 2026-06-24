"""Filesystem tools: read/write/edit files, list directories, glob.

Paths are resolved relative to ctx.cfg.active_dir (the kernel tree by default) unless
absolute. Every function returns a string and is wrapped by registry.dispatch so it
never raises out of the loop.
"""
import os
import glob as globmod

from .spec import schema, truncate


def _resolve(ctx, path: str | None) -> str:
    if not path:
        return ctx.cfg.active_dir
    return path if os.path.isabs(path) else os.path.join(ctx.cfg.active_dir, path)


def _expand_braces(pat: str) -> list:
    """Expand shell-style {a,b,c} alternations (Python's glob does NOT) — `*.{c,h}` -> *.c, *.h."""
    i = pat.find("{")
    if i < 0:
        return [pat]
    j = pat.find("}", i)
    if j < 0:
        return [pat]
    pre, body, post = pat[:i], pat[i + 1:j], pat[j + 1:]
    out = []
    for alt in body.split(","):
        out.extend(_expand_braces(pre + alt + post))
    return out


def read_file(ctx, path: str, offset: int = 1, limit: int = 250) -> str:
    p = _resolve(ctx, path)
    if not os.path.exists(p):
        return f"(error: no such file: {p})"
    if os.path.isdir(p):
        return f"(note: {p} is a directory — listing it instead)\n" + list_dir(ctx, path)
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return f"(error: cannot read {p}: {e})"
    offset = max(1, int(offset or 1))
    end = offset - 1 + int(limit or 2000)
    chunk = lines[offset - 1:end]
    if not chunk:
        return f"(file has {len(lines)} lines; offset {offset} is past the end)"
    width = len(str(offset - 1 + len(chunk)))
    body = "".join(f"{offset + i:>{width}}\t{ln.rstrip(chr(10))}\n" for i, ln in enumerate(chunk))
    note = "" if end >= len(lines) else f"\n... [{len(lines) - end} more lines; raise limit/offset] ..."
    return truncate(f"# {p}  ({len(lines)} lines)\n{body}{note}")


def write_file(ctx, path: str, content: str = "") -> str:
    p = _resolve(ctx, path)
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content or "")
    except Exception as e:
        return f"(error: cannot write {p}: {e})"
    return f"wrote {len(content or '')} chars to {p}"


def edit_file(ctx, path: str, old: str, new: str, replace_all: bool = False) -> str:
    p = _resolve(ctx, path)
    if not os.path.exists(p):
        return f"(error: no such file: {p})"
    try:
        with open(p, encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        return f"(error: cannot read {p}: {e})"
    if old == new:
        return "(error: old and new are identical)"
    n = text.count(old)
    if n == 0:
        return f"(error: `old` text not found in {p}. Read the file and match exactly, incl. indentation.)"
    if n > 1 and not replace_all:
        return (f"(error: `old` matches {n} places in {p}; make it unique or pass replace_all=true.)")
    new_text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(new_text)
    except Exception as e:
        return f"(error: cannot write {p}: {e})"
    return f"edited {p}: replaced {n if replace_all else 1} occurrence(s)"


def list_dir(ctx, path: str = ".") -> str:
    p = _resolve(ctx, path)
    if not os.path.isdir(p):
        return f"(error: not a directory: {p})"
    try:
        entries = sorted(os.listdir(p))
    except Exception as e:
        return f"(error: cannot list {p}: {e})"
    rows = []
    for name in entries:
        full = os.path.join(p, name)
        if os.path.isdir(full):
            rows.append(f"  {name}/")
        else:
            try:
                rows.append(f"  {name}  ({os.path.getsize(full)} B)")
            except OSError:
                rows.append(f"  {name}")
    return truncate(f"# {p}  ({len(entries)} entries)\n" + "\n".join(rows))


def glob_files(ctx, pattern: str, path: str = "", limit: int = 300) -> str:
    base = ctx.cfg.active_dir
    if path:                                       # optional subtree scope (models reach for this)
        base = path if os.path.isabs(path) else os.path.join(base, path)
    matches = []
    for pat in _expand_braces(pattern):            # {c,h} alternations (glob.glob can't)
        full = pat if os.path.isabs(pat) else os.path.join(base, pat)
        try:
            matches.extend(globmod.glob(full, recursive=True))
        except Exception as e:
            return f"(error: bad glob {pattern!r}: {e})"
    matches = sorted(set(matches))
    shown = matches[:limit]
    extra = "" if len(matches) <= limit else f"\n... [{len(matches) - limit} more]"
    return truncate(f"{len(matches)} match(es) for {pattern!r}:\n" + "\n".join(shown) + extra)


TOOLS = [
    ("read_file", read_file, schema(
        "read_file",
        "Read a text file (line-numbered). Paths are relative to the active tree unless absolute.",
        {"path": {"type": "string", "description": "file path (relative to the active kernel tree or absolute)."},
         "offset": {"type": "integer", "description": "1-based first line (default 1).", "default": 1},
         "limit": {"type": "integer", "description": "max lines to return (default 250); page with offset.",
                   "default": 250}},
        ["path"])),
    ("write_file", write_file, schema(
        "write_file",
        "Create or OVERWRITE a file with the given content (parent dirs created). For small edits prefer edit_file.",
        {"path": {"type": "string"}, "content": {"type": "string"}},
        ["path", "content"])),
    ("edit_file", edit_file, schema(
        "edit_file",
        "Replace an exact substring in a file. `old` must match verbatim (incl. indentation) and be unique "
        "unless replace_all=true.",
        {"path": {"type": "string"},
         "old": {"type": "string", "description": "exact existing text to replace."},
         "new": {"type": "string", "description": "replacement text."},
         "replace_all": {"type": "boolean", "description": "replace every occurrence (default false).", "default": False}},
        ["path", "old", "new"])),
    ("list_dir", list_dir, schema(
        "list_dir", "List a directory's entries (dirs marked with /, files with size).",
        {"path": {"type": "string", "description": "directory (default '.').", "default": "."}})),
    ("glob", glob_files, schema(
        "glob", "Find files matching a shell glob (recursive ** and {a,b} braces supported), relative "
        "to the active tree (or to `path`).",
        {"pattern": {"type": "string", "description": "e.g. 'kernel/sched/*.c' or '**/futex*.c' or '**/*.{c,h}'."},
         "path": {"type": "string", "description": "optional subtree to scope the search (relative to the active tree).",
                  "default": ""},
         "limit": {"type": "integer", "default": 300}},
        ["pattern"])),
]
