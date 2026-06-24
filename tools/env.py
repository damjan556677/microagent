"""Host tool/binary availability ("doctor"). microagent's tools shell out to external binaries;
this reports which are present so the system prompt can tell the agent to PREFER available native
tools (instead of shelling out to `run grep/find`), and `/tools` can flag what's missing.
"""
import shutil

# (binary, what it powers) — ordered roughly by how much the agent relies on it.
_TOOLBINS = [
    ("rg",       "fast `search` (ripgrep)"),
    ("grep",     "`search` fallback"),
    ("clangd",   "semantic nav: find_symbol / references / hover / outline"),
    ("cscope",   "`cscope` symbol search"),
    ("ctags",    "`ctags` symbol lookup"),
    ("make",     "build_linux / build_index"),
    ("ssh",      "deploy_qemu / ssh_exec"),
    ("rsync",    "remote file sync"),
]


def check_tools():
    """Return (present, missing) — each a list of (binary, capability)."""
    present, missing = [], []
    for b, cap in _TOOLBINS:
        (present if shutil.which(b) else missing).append((b, cap))
    return present, missing


def capability_summary() -> str:
    """One line for the system prompt: which native tools exist on this host."""
    present, missing = check_tools()
    have = ", ".join(b for b, _ in present) or "(none)"
    s = f"Native tools available on this host: {have}."
    if missing:
        s += " Missing: " + ", ".join(b for b, _ in missing) + "."
    return s


def report_lines():
    """Present/missing lines (with capability labels) for the `/tools` command."""
    present, missing = check_tools()
    out = []
    for b, cap in present:
        out.append(f"  ✓ {b:<8} {cap}")
    for b, cap in missing:
        out.append(f"  ✗ {b:<8} {cap}  (missing)")
    return out
