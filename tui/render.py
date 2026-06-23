"""ANSI renderer: turns the agent's normalized event stream into a warm-colored,
scrolling terminal transcript with a live spinner/status line.

A single daemon thread animates the spinner on the bottom line while the loop is busy
(model call / tool run). All transcript writes go through the lock and clear the spinner
line first, so streamed tokens and the spinner never collide.
"""
import itertools
import json
import re
import shutil
import sys
import threading
import time

from agent import events as E
from . import palette as P


def _term_width(default=80):
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except Exception:
        return default


def _one_line(s: str, limit: int = 100) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_INLINE = re.compile(r"\*\*(?P<b>.+?)\*\*|`(?P<c>[^`\n]+)`")


def render_md_line(line: str) -> str:
    """Render one line of inline markdown to ANSI: **bold**, `code`, and # headings.
    With color disabled, strip the markers for clean plain text."""
    h = _MD_HEADING.match(line.strip())
    if h:
        return P.paint(h.group(2), P.GOLD, bold=True) if P.ENABLED else h.group(2)
    if not P.ENABLED:
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        return re.sub(r"`([^`\n]+)`", r"\1", line)
    base = P.fg(P.OFFWHITE)

    def repl(m):
        if m.group("b") is not None:                      # **bold** -> bold, resume base
            return P.fg(P.OFFWHITE, bold=True) + m.group("b") + P.RESET + base
        return P.fg(P.AMBER) + m.group("c") + P.RESET + base   # `code` -> amber

    return base + _MD_INLINE.sub(repl, line) + P.RESET


class Console:
    def __init__(self, cfg, stream=None):
        self.cfg = cfg
        self.out = stream or sys.stdout
        self.show_thinking = cfg.show_thinking
        self._lock = threading.RLock()
        self._frames = itertools.cycle(P.SPIN)
        self._spin_label = None
        self._spin_on = False
        self._streaming = False           # mid output stream (guards the spinner)
        self._mode = None                 # 'thinking' | 'text' | None
        self._linebuf = ""                # buffered partial line of assistant text
        self._text_started = False        # printed the ● marker for this message yet?
        self._in_fence = False            # inside a ``` code fence
        self._thinking_open = False       # a thinking char-stream line is open
        self._t0 = time.time()
        self._stop = False
        self._isatty = self.out.isatty()
        self._thr = threading.Thread(target=self._spin_loop, daemon=True)
        self._thr.start()

    # ---------------------------------------------------------------- spinner
    def _spin_loop(self):
        period = 1.0 / max(1, self.cfg.spinner_hz)
        while not self._stop:
            with self._lock:
                if self._spin_on and not self._streaming and self._isatty:
                    frame = next(self._frames)
                    dt = time.time() - self._t0
                    txt = f"{frame} {self._spin_label or 'working'}  ({dt:.0f}s)"
                    self.out.write("\r" + P.paint(txt, P.AMBER) + "\x1b[K")
                    self.out.flush()
            time.sleep(period)

    def _clear_line(self):
        if self._isatty:
            self.out.write("\r\x1b[K")
            self.out.flush()

    def _end_stream(self):
        if self._thinking_open:
            self.out.write("\n")
            self._thinking_open = False
        if self._linebuf != "":           # flush a trailing partial line of text
            self._emit_text_line(self._linebuf)
            self._linebuf = ""
        self._mode = None
        self._text_started = False
        self._streaming = False

    def spinner(self, label: str):
        with self._lock:
            self._end_stream()
            self._spin_label = label
            self._spin_on = True
            self._t0 = time.time()

    def stop_spinner(self):
        with self._lock:
            if self._spin_on:
                self._clear_line()
            self._spin_on = False

    # ---------------------------------------------------------------- writing
    def line(self, s: str = ""):
        with self._lock:
            self._spin_on = False
            self._clear_line()
            self._end_stream()
            self.out.write(s + "\n")
            self.out.flush()

    def stream(self, kind: str, text: str):
        """Stream tokens. Thinking is rendered char-by-char (dim); assistant text is
        line-buffered so inline markdown (**bold**, `code`, headings) can be rendered
        even when the markers split across token deltas."""
        if not text:
            return
        with self._lock:
            if self._spin_on:
                self._clear_line()
                self._spin_on = False
            if kind != self._mode:                       # mode switch: close the previous
                if self._thinking_open:
                    self.out.write("\n")
                    self._thinking_open = False
                if self._mode == "text" and self._linebuf != "":
                    self._emit_text_line(self._linebuf)
                    self._linebuf = ""
                self._mode = kind
            self._streaming = True
            if kind == "thinking":
                if not self._thinking_open:
                    self.out.write(P.paint("· thinking ", P.DIM, italic=True))
                    self._thinking_open = True
                self.out.write(P.paint(text, P.DIM, italic=True))
            else:
                self._linebuf += text
                while "\n" in self._linebuf:
                    ln, _, self._linebuf = self._linebuf.partition("\n")
                    self._emit_text_line(ln)
            self.out.flush()

    def _emit_text_line(self, line: str):
        """Render one complete line of assistant text (markdown -> ANSI)."""
        prefix = P.paint("● ", P.GOLD, bold=True) if not self._text_started else "  "
        self._text_started = True
        if line.strip().startswith("```"):
            self._in_fence = not self._in_fence
            self.out.write(prefix + P.paint(line, P.DIM) + "\n")
        elif self._in_fence:
            self.out.write(prefix + P.paint(line, P.OFFWHITE) + "\n")
        else:
            self.out.write(prefix + render_md_line(line) + "\n")

    # ---------------------------------------------------------------- events
    def banner(self, model_label: str):
        w = min(_term_width(), 78)
        self.line()
        self.line(P.paint("  microagent ", P.GOLD, bold=True) +
                  P.paint("· stdlib kernel agent", P.DIM))
        self.line(P.paint(f"  model {model_label}   tree {self.cfg.active_dir}", P.DIM))
        self.line(P.paint(f"  ssh {self.cfg.ssh.target}   effort {self.cfg.reasoning_effort}", P.DIM))
        self.line(P.rule("─", w))
        self.line(P.paint("  type a task · /help for commands · /quit to exit", P.DIM))
        self.line()

    def tool_call(self, ev: E.ToolCall):
        inp = ev.input or {}
        if ev.name == "run":
            summary = _one_line(inp.get("command", ""), 90)
        elif ev.name in ("read_file", "write_file", "edit_file", "list_dir", "glob"):
            summary = _one_line(str(inp.get("path") or inp.get("pattern") or ""), 70)
        else:
            summary = _one_line(json.dumps(inp), 90) if inp else ""
        self.line(P.paint("  → ", P.AMBER, bold=True) +
                  P.paint(ev.name, P.AMBER, bold=True) +
                  (P.paint("  " + summary, P.DIM) if summary else ""))

    def tool_result(self, ev: E.ToolResult, max_lines: int = 16):
        text = ev.text or ""
        ok = ev.ok and not text.lstrip().startswith("(error") and not text.lstrip().startswith("(blocked")
        glyph = P.paint("✓", P.GREEN, bold=True) if ok else P.paint("✗", P.RED, bold=True)
        lines = text.splitlines()
        head = lines[:max_lines]
        body = "\n".join("    " + P.paint(ln, P.DIM) for ln in head)
        more = "" if len(lines) <= max_lines else P.paint(f"\n    … [{len(lines) - max_lines} more lines]", P.DIM)
        self.line(f"  {glyph} " + P.paint(f"{ev.name}", P.DIM))
        if body:
            self.line(body + more)

    def nudge(self, ev: E.Nudge):
        self.line(P.paint("  ↻ " + _one_line(ev.text, 110), P.RED, italic=True))

    def done(self, ev: E.Done, n_tools: int = 0):
        bits = [f"{n_tools} tool call(s)"]
        if ev.usage:
            tot = ev.usage.get("total_tokens")
            if tot:
                bits.append(f"{tot} tok")
            cost = ev.usage.get("cost")
            if cost:
                bits.append(f"${cost:.4f}")
        if ev.cost and not (ev.usage and ev.usage.get("cost")):
            bits.append(f"${ev.cost:.4f}")
        tag = "done" if ev.reason == "stop" else ev.reason
        self.line(P.paint(f"  ◆ {tag} — " + " · ".join(bits), P.GOLD, bold=True))
        self.line()

    def system(self, msg: str):
        self.line(P.paint("  " + msg, P.DIM))

    def error(self, msg: str):
        self.line(P.paint("  ! " + msg, P.RED, bold=True))

    def render_event(self, ev, counters: dict):
        if isinstance(ev, E.Status):
            self.spinner(ev.label)
        elif isinstance(ev, E.StreamDelta):
            if ev.kind == "thinking" and not self.show_thinking:
                return
            self.stream(ev.kind, ev.text)
        elif isinstance(ev, E.ToolCall):
            counters["tools"] = counters.get("tools", 0) + 1
            self.tool_call(ev)
        elif isinstance(ev, E.ToolResult):
            self.tool_result(ev)
        elif isinstance(ev, E.Nudge):
            self.nudge(ev)
        elif isinstance(ev, E.AssistantText):
            if ev.text.strip():
                self.line(P.paint("  " + ev.text.strip(), P.OFFWHITE))
        elif isinstance(ev, E.Thinking):
            if self.show_thinking and ev.text.strip():
                self.line(P.paint("  " + _one_line(ev.text, 200), P.DIM, italic=True))
        elif isinstance(ev, E.Done):
            self.done(ev, counters.get("tools", 0))

    def close(self):
        self._stop = True
        self.stop_spinner()
