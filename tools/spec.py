"""Shared tool plumbing: ToolContext, the OpenAI schema helper, output truncation.

Kept separate from registry.py so tool modules (fs/search/shell/kernel/...) can import
these helpers without a circular import (registry imports the tool modules).
"""
from dataclasses import dataclass, field

# Cap on a single tool result handed back to the model. Kept modest because every tool
# result is resent in full on every subsequent turn — a few large outputs blow up context
# (and cost) super-linearly. Tools that need more should narrow (ranges/grep/counts).
MAX_OUTPUT = 8000


@dataclass
class ToolContext:
    """Everything a tool needs: config + an optional human-confirm callback.

    `confirm(prompt)->bool` is supplied by the REPL so destructive (gated) commands
    can ask the user. In non-interactive/one-shot mode it is None, and gated commands
    are auto-denied (the tool returns an explanatory error rather than blocking).
    `extra` stashes long-lived helpers (e.g. the persistent clangd client).
    """
    cfg: object
    confirm: object = None
    extra: dict = field(default_factory=dict)

    def ask(self, prompt: str) -> bool:
        if self.confirm is None:
            return False
        try:
            return bool(self.confirm(prompt))
        except Exception:
            return False


def schema(name, description, properties=None, required=None) -> dict:
    """Build an OpenAI function-tool schema (mirrors ebpf-opt4 tools._schema_static)."""
    fn = {"name": name, "description": description,
          "parameters": {"type": "object", "properties": properties or {}}}
    if required:
        fn["parameters"]["required"] = required
    return {"type": "function", "function": fn}


def truncate(s: str, n: int = MAX_OUTPUT) -> str:
    """Trim a long result, keeping the head and tail with a marker in between."""
    s = s if isinstance(s, str) else str(s)
    if len(s) <= n:
        return s
    head = n * 2 // 3
    tail = n - head
    omitted = len(s) - head - tail
    return (s[:head] + f"\n\n... [{omitted} chars omitted] ...\n\n" + s[-tail:])
