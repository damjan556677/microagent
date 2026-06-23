"""The synchronous agent loop: a generator that drives the model + tools and yields
the normalized event stream the TUI renders.

Port of /amd4/cpu/ebpf-opt4/agentic.py:472-585, made synchronous (no asyncio) and
decoupled from any specific tool set: the caller passes the tool schemas, a
ToolContext, and a dispatch callable. A conservative "finish-the-job" nudge re-prompts
the model only when it leaves a build/deploy workflow half-done.
"""
from . import llm, history
from .events import (Status, ToolCall, ToolResult, Nudge, Done)
from .llm import Completion

_SOURCE_SUFFIXES = (".c", ".h", ".S", ".s", "Kconfig", "Kbuild", "Makefile",
                    ".config", "defconfig")


def _is_source(path: str) -> bool:
    p = path or ""
    return p.endswith(_SOURCE_SUFFIXES) or "/configs/" in p or "defconfig" in p


def _accum(tot: dict, usage: dict | None):
    if not usage:
        return
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        tot[k] = tot.get(k, 0) + int(usage.get(k, 0) or 0)
    if usage.get("cost"):
        tot["cost"] = tot.get("cost", 0.0) + float(usage["cost"])


def _nudge_text(state: dict) -> str | None:
    """Conservative nudges — only fire inside an obviously half-done kernel workflow."""
    if state.get("edited_src") and not state.get("built"):
        return ("You modified kernel source/config but haven't rebuilt. Run build_linux (or "
                "`make`) to verify it compiles, then report the outcome — don't stop yet.")
    if state.get("deployed") and not state.get("verified"):
        return ("You deployed a kernel but haven't verified it booted. Run ssh_exec `uname -r` "
                "(or qemu_console) on the target to confirm, then report before/after.")
    return None


def _update_state(state: dict, name: str, args: dict, result: str, ok: bool):
    if not ok:
        return
    if name in ("write_file", "edit_file") and _is_source(str(args.get("path", ""))):
        state["edited_src"] = True
    elif name == "kconfig" and args.get("op") in ("enable", "disable", "module", "set"):
        state["edited_src"] = True   # only MUTATING kconfig ops count; `get` is read-only
    elif name == "build_linux":
        state["built"] = True
    elif name == "run" and "make" in (args.get("command", "")) and "exit=0" in result:
        state["built"] = True
    elif name == "deploy_qemu":
        state["deployed"] = True
    elif name in ("ssh_exec", "qemu_console"):
        state["verified"] = True


def run(cfg, ctx, messages, tools_schema, dispatch, max_turns=None, nudge=None):
    """Drive the loop. `messages` is mutated in place (the running conversation).

    Yields: Status, StreamDelta, ToolCall, ToolResult, Nudge, Done.
    """
    cap = max_turns or cfg.max_turns
    nudge_max = cfg.nudge if nudge is None else nudge
    nudges_used = 0
    usage_tot = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0.0}
    state: dict = {}

    for _turn in range(cap):
        yield Status("thinking")
        final = None
        for item in llm.stream_complete(cfg, messages, tools_schema):
            if isinstance(item, Completion):
                final = item
            else:
                yield item                      # StreamDelta -> live render
        if final is None or final.error:
            yield Done(usage=usage_tot, reason="error")
            return

        _accum(usage_tot, final.usage)
        content = llm.strip_markup(final.content)
        messages.append(history.assistant_dict(content, final.tool_calls))

        if not final.tool_calls:
            txt = _nudge_text(state) if nudges_used < nudge_max else None
            if txt:
                nudges_used += 1
                yield Nudge(txt)
                messages.append({"role": "user", "content": txt})
                continue
            yield Done(usage=usage_tot, reason="stop")
            return

        for tc in final.tool_calls:
            args, err = history.parse_args(tc.arguments)
            if err:
                msg = (f"(error: could not parse arguments for {tc.name} as a JSON object "
                       f"({err}). Resend the call with valid JSON arguments.)")
                yield ToolCall(tc.id, tc.name, {})
                yield ToolResult(tc.id, tc.name, msg, ok=False)
                messages.append(history.tool_result_msg(tc.id, tc.name, msg))
                continue
            yield ToolCall(tc.id, tc.name, args)
            yield Status(f"running {tc.name}")
            result = dispatch(ctx, tc.name, args)
            ok = not (result.lstrip().startswith("(error") or result.lstrip().startswith("(blocked"))
            yield ToolResult(tc.id, tc.name, result, ok=ok)
            messages.append(history.tool_result_msg(tc.id, tc.name, result))
            _update_state(state, tc.name, args, result, ok)

    yield Done(usage=usage_tot, reason="max_turns")
