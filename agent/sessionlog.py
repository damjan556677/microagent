"""Optional JSONL session logger for eval/analysis — additive to the TUI, never alters it.

One JSON object per line: a `task_start`, then the structured event stream (tool calls/results,
nudges, ctx, done — raw token `StreamDelta`s are skipped), then a `summary` and a final
`conversation` dump of the full message list. Consumed by eval/analyze.py.
"""
import json
import time
from dataclasses import asdict, is_dataclass


class SessionLogger:
    def __init__(self, path: str):
        self.f = open(path, "a", encoding="utf-8")
        self._t0 = time.time()
        self._tools = 0
        self._fails = 0
        self._ctx_used = 0
        self._ctx_total = 0
        self._reason = None
        self._usage = None

    def _write(self, rec: dict):
        self.f.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")
        self.f.flush()

    def start(self, task: str, model: str):
        self._t0 = time.time()
        self._write({"type": "task_start", "task": task, "model": model})

    def event(self, ev):
        name = type(ev).__name__
        if name == "StreamDelta":
            return                       # token spam — not useful for analysis
        rec = {"type": name}
        if is_dataclass(ev):
            rec.update(asdict(ev))
        if name == "ToolCall":
            self._tools += 1
        elif name == "ToolResult" and not getattr(ev, "ok", True):
            self._fails += 1
        elif name == "Ctx":
            self._ctx_used, self._ctx_total = ev.used, ev.total
        elif name == "Done":
            self._reason, self._usage = ev.reason, ev.usage
        self._write(rec)

    def finish(self, messages: list):
        pct = round(100.0 * self._ctx_used / self._ctx_total, 1) if self._ctx_total else None
        self._write({"type": "summary", "tool_calls": self._tools, "tool_failures": self._fails,
                     "ctx_used": self._ctx_used, "ctx_total": self._ctx_total, "ctx_pct": pct,
                     "usage": self._usage, "reason": self._reason,
                     "wall_s": round(time.time() - self._t0, 2)})
        self._write({"type": "conversation", "messages": messages})

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass
