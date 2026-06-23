"""Semantic navigation via a persistent clangd LSP client (stdlib JSON-RPC over stdio).

clangd gives precise, type-aware navigation (definitions/references/hover/outline) that
grep and ctags can't — it understands `static` scope, macros, and overloads. It consumes
compile_commands.json (build it with build_index). If clangd isn't installed the tools
degrade with a clear message pointing at cscope/ctags.

The client is spawned lazily, cached on the ToolContext, and torn down at process exit.
"""
import atexit
import json
import os
import shutil
import subprocess
import threading
import time

from .spec import schema, truncate


def _uri(path):
    return "file://" + os.path.abspath(path)


def _path(uri):
    return uri[7:] if uri.startswith("file://") else uri


class ClangdClient:
    def __init__(self, root):
        self.root = root
        args = ["clangd", "--background-index", f"--compile-commands-dir={root}", "--log=error"]
        self.proc = subprocess.Popen(args, cwd=root, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self._id = 0
        self._wlock = threading.Lock()
        self._resp = {}
        self._events = {}
        self._opened = set()
        self._diag = {}          # uri -> Event set when clangd finishes parsing that file
        self._alive = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        atexit.register(self.shutdown)
        self._initialize()

    def _write(self, msg):
        data = json.dumps(msg).encode()
        header = f"Content-Length: {len(data)}\r\n\r\n".encode()
        with self._wlock:
            try:
                self.proc.stdin.write(header + data)
                self.proc.stdin.flush()
            except Exception:
                self._alive = False

    def _read_loop(self):
        f = self.proc.stdout
        while self._alive:
            try:
                headers = {}
                while True:
                    line = f.readline()
                    if not line:
                        self._alive = False
                        return
                    s = line.decode(errors="replace").strip()
                    if s == "":
                        break
                    if ":" in s:
                        k, v = s.split(":", 1)
                        headers[k.strip().lower()] = v.strip()
                n = int(headers.get("content-length", 0) or 0)
                body = f.read(n) if n else b""
                msg = json.loads(body.decode(errors="replace"))
            except Exception:
                continue
            mid = msg.get("id")
            method = msg.get("method")
            if mid is not None and ("result" in msg or "error" in msg):
                self._resp[mid] = msg
                ev = self._events.get(mid)
                if ev:
                    ev.set()
            elif mid is not None and method:                 # server->client request: ack it
                self._write({"jsonrpc": "2.0", "id": mid, "result": None})
            elif method == "textDocument/publishDiagnostics":  # file finished parsing
                uri = (msg.get("params") or {}).get("uri")
                ev = self._diag.get(uri)
                if ev:
                    ev.set()

    def _request(self, method, params, timeout=60):
        if not self._alive:
            return None
        with self._wlock:
            self._id += 1
            mid = self._id
        ev = threading.Event()
        self._events[mid] = ev
        self._write({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        if not ev.wait(timeout):
            self._events.pop(mid, None)
            return None
        self._events.pop(mid, None)
        msg = self._resp.pop(mid, None)
        return (msg or {}).get("result")

    def _notify(self, method, params):
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _initialize(self):
        self._request("initialize", {
            "processId": os.getpid(), "rootUri": _uri(self.root),
            "capabilities": {"textDocument": {"definition": {}, "references": {},
                                              "hover": {"contentFormat": ["plaintext", "markdown"]},
                                              "documentSymbol": {}},
                             "workspace": {"symbol": {}}}}, timeout=60)
        self._notify("initialized", {})

    def open(self, path, parse_timeout=90):
        ap = os.path.abspath(path)
        if ap in self._opened:
            return True
        try:
            text = open(ap, errors="replace").read()
        except Exception:
            return False
        lang = "c" if ap.endswith((".c", ".h")) else "cpp"
        uri = _uri(ap)
        ev = threading.Event()
        self._diag[uri] = ev
        self._notify("textDocument/didOpen",
                     {"textDocument": {"uri": uri, "languageId": lang, "version": 1, "text": text}})
        self._opened.add(ap)
        # Block until clangd reports it finished parsing this TU (preamble can take 10-60s
        # the first time on kernel headers); otherwise queries race ahead and return empty.
        ev.wait(parse_timeout)
        return True

    def shutdown(self):
        if not self._alive:
            return
        self._alive = False
        try:
            self.proc.terminate()
        except Exception:
            pass


# ---------------------------------------------------------------- tool helpers
def _client(ctx):
    """Lazily spawn (and cache) a clangd client for the active tree, or None if unavailable."""
    if not shutil.which("clangd"):
        return None
    cache = ctx.extra.setdefault("clangd", {})
    root = ctx.cfg.active_dir
    cli = cache.get(root)
    if cli is None or not cli._alive:
        try:
            cli = ClangdClient(root)
        except Exception:
            return None
        cache[root] = cli
    return cli


_NOPE = ("(clangd not available for semantic nav — install clangd, or use cscope/ctags "
         "(build_index first) / search instead.)")


def _resolve(ctx, path):
    return path if os.path.isabs(path) else os.path.join(ctx.cfg.active_dir, path)


def _loc_str(loc):
    uri = loc.get("uri", "")
    rng = loc.get("range", {}).get("start", {})
    return f"{_path(uri)}:{rng.get('line', 0) + 1}:{rng.get('character', 0) + 1}"


def find_symbol(ctx, query: str, limit: int = 40) -> str:
    cli = _client(ctx)
    if cli is None:
        return _NOPE
    res = cli._request("workspace/symbol", {"query": query}, timeout=60) or []
    if not res:
        return f"clangd: no symbol matches for {query!r} (is compile_commands.json built? run build_index)"
    rows = []
    for s in res[:limit]:
        loc = s.get("location", {})
        rows.append(f"{_loc_str(loc)}  {s.get('name')} (kind {s.get('kind')})")
    return truncate(f"clangd workspace symbols for {query!r}:\n" + "\n".join(rows))


def references(ctx, symbol: str, limit: int = 80) -> str:
    cli = _client(ctx)
    if cli is None:
        return _NOPE
    syms = cli._request("workspace/symbol", {"query": symbol}, timeout=60) or []
    exact = [s for s in syms if s.get("name") == symbol] or syms
    if not exact:
        return f"clangd: symbol {symbol!r} not found"
    loc = exact[0].get("location", {})
    path = _path(loc.get("uri", ""))
    start = loc.get("range", {}).get("start", {})
    if not cli.open(path):
        return f"(error: cannot open {path})"
    res = cli._request("textDocument/references", {
        "textDocument": {"uri": _uri(path)},
        "position": {"line": start.get("line", 0), "character": start.get("character", 0)},
        "context": {"includeDeclaration": True}}, timeout=60) or []
    if not res:
        return f"clangd: no references to {symbol!r}"
    rows = [_loc_str(r) for r in res[:limit]]
    extra = "" if len(res) <= limit else f"\n... [{len(res) - limit} more]"
    return truncate(f"clangd references to {symbol!r} ({len(res)}):\n" + "\n".join(rows) + extra)


def hover(ctx, file: str, line: int, column: int = 1) -> str:
    cli = _client(ctx)
    if cli is None:
        return _NOPE
    path = _resolve(ctx, file)
    if not cli.open(path):
        return f"(error: cannot open {path})"
    res = cli._request("textDocument/hover", {
        "textDocument": {"uri": _uri(path)},
        "position": {"line": int(line) - 1, "character": int(column) - 1}}, timeout=45)
    if not res:
        return f"clangd: no hover info at {file}:{line}:{column}"
    contents = res.get("contents", "")
    if isinstance(contents, dict):
        contents = contents.get("value", "")
    elif isinstance(contents, list):
        contents = "\n".join(c.get("value", "") if isinstance(c, dict) else str(c) for c in contents)
    return truncate(f"hover {file}:{line}:{column}:\n{contents}")


def outline(ctx, file: str, limit: int = 200) -> str:
    cli = _client(ctx)
    if cli is None:
        return _NOPE
    path = _resolve(ctx, file)
    if not cli.open(path):
        return f"(error: cannot open {path})"
    res = cli._request("textDocument/documentSymbol", {"textDocument": {"uri": _uri(path)}}, timeout=45) or []
    rows = []

    def walk(syms, depth=0):
        for s in syms:
            # DocumentSymbol has selectionRange/range; SymbolInformation has location.range.
            rng = (s.get("selectionRange") or s.get("range")
                   or s.get("location", {}).get("range") or {})
            start = rng.get("start", {})
            rows.append(f"{'  ' * depth}{start.get('line', 0) + 1}\t{s.get('name')} (kind {s.get('kind')})")
            walk(s.get("children", []), depth + 1)
    walk(res)
    if not rows:
        return f"clangd: no symbols in {file}"
    return truncate(f"outline of {file}:\n" + "\n".join(rows[:limit]))


TOOLS = [
    ("find_symbol", find_symbol, schema(
        "find_symbol",
        "Find where a symbol (function/struct/macro/var) is DEFINED across the tree, type-aware, via "
        "clangd. More precise than cscope for static/overloaded names. Needs compile_commands.json "
        "(build_index).",
        {"query": {"type": "string", "description": "symbol name or substring."},
         "limit": {"type": "integer", "default": 40}},
        ["query"])),
    ("references", references, schema(
        "references",
        "Find all USES of a symbol across the tree (semantic, via clangd). Resolves the symbol then "
        "lists every reference as file:line.",
        {"symbol": {"type": "string", "description": "exact symbol name."},
         "limit": {"type": "integer", "default": 80}},
        ["symbol"])),
    ("hover", hover, schema(
        "hover",
        "Type/signature/doc info for the token at file:line:column (1-based), via clangd.",
        {"file": {"type": "string"}, "line": {"type": "integer"},
         "column": {"type": "integer", "default": 1}},
        ["file", "line"])),
    ("outline", outline, schema(
        "outline", "List the symbols defined in a file (functions/structs/...) with line numbers, via clangd.",
        {"file": {"type": "string"}, "limit": {"type": "integer", "default": 200}},
        ["file"])),
]
