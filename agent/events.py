"""Normalized event model shared by the LLM client, the agent loop, and the TUI.

Ported from /amd4/cpu/ebpf-opt4/agentic.py:290-319. The loop yields a stream of
these; the TUI renders them. Keeping the LLM/response parsing decoupled from
rendering is what lets the same loop drive both the REPL and a one-shot run.
"""
from dataclasses import dataclass, field


@dataclass
class AssistantText:
    """A chunk (or whole) of the model's user-facing narration."""
    text: str


@dataclass
class Thinking:
    """A chunk (or whole) of the model's reasoning/thinking trace."""
    text: str


@dataclass
class ToolCall:
    """The model asked to run a tool."""
    id: str
    name: str
    input: dict


@dataclass
class ToolResult:
    """The text result handed back to the model for a ToolCall."""
    id: str
    name: str
    text: str
    ok: bool = True          # False -> render with the red ✗ glyph


@dataclass
class Nudge:
    """A finish-the-job re-prompt the loop injected because the model stopped early."""
    text: str


@dataclass
class Done:
    """Terminal event: the turn/run finished. Carries token usage + reported cost."""
    usage: dict | None = None
    cost: float | None = None
    reason: str = "stop"     # stop | max_turns | error


@dataclass
class Status:
    """A spinner phase label the loop emits before a blocking step (model call / tool run).
    The TUI shows an animated spinner with this label until the next content event."""
    label: str


@dataclass
class StreamDelta:
    """Low-level streaming token delta (kind = 'thinking' | 'text').

    The LLM client emits these live during a single completion so the TUI can
    paint tokens as they arrive; the loop coalesces them into Thinking /
    AssistantText for history.
    """
    kind: str
    text: str
