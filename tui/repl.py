"""Interactive REPL: readline input + slash-commands. Plain text is a task for the agent.

Slash-commands:
  /help                 show this help
  /model [port|alias]   show/switch model: a port (8006, 8003, 8002) or an OpenRouter alias
                        (deepseek, opus, sonnet, glm). No arg lists the available selectors.
  /effort [low|med|high]show or set reasoning effort (OpenRouter only; ignored by internal)
  /cd [path]            show or change the active kernel tree
  /index                build/refresh the cscope + compile_commands index of the active tree
  /raw                  toggle display of the model's reasoning stream
  /tools                list available tools
  /reset                clear the conversation (keep settings)
  /quit                 exit  (also /q, /exit, Ctrl-D)
"""
import os
import readline  # noqa: F401 — importing enables line editing + history

from agent import llm
from tools import registry, env
from . import palette as P

HELP = (
    "commands: /help  /model [port|alias]  /effort [l|m|h]  /cd [path]  /index  /raw  "
    "/tools  /reset  /quit"
)


def make_confirm(console):
    """A confirm callback for the destructive-op gate (REPL only)."""
    def confirm(prompt: str) -> bool:
        console.stop_spinner()
        console.line(P.paint(prompt, P.RED, bold=True))
        try:
            ans = input(P.paint("  allow? [y/N] ", P.AMBER, bold=True))
        except (EOFError, KeyboardInterrupt):
            return False
        return ans.strip().lower() in ("y", "yes")
    return confirm


def _handle_command(session, console, line: str) -> bool:
    """Run a slash-command. Return False to quit the REPL, True to continue."""
    parts = line.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    cfg = session.cfg

    if cmd in ("/quit", "/q", "/exit"):
        return False
    if cmd == "/help":
        console.system(HELP)
    elif cmd == "/model":
        if arg:
            cfg.model = arg
            session.refresh_system()
            ep = llm.resolve_endpoint(cfg)
            ctxs = f"  · ctx {ep.max_ctx:,}" if ep.max_ctx else ""
            console.system(f"model → {ep.label}{ctxs}")
        else:
            ep = llm.resolve_endpoint(cfg)
            ctxs = f"  · ctx {ep.max_ctx:,}" if ep.max_ctx else ""
            console.system(f"model = {ep.label}{ctxs}")
            ports = "  ".join(
                (f"{p} ({s.alias})" if s.alias else str(p))
                for p, s in sorted(cfg.internal.ports.items()))
            if ports:
                console.system(f"  internal ports: {ports}")
            if cfg.internal.aliases:
                console.system("  internal aliases: " + "  ".join(sorted(cfg.internal.aliases)))
            console.system("  openrouter:     " + "  ".join(sorted(llm.OPENROUTER)))
    elif cmd == "/effort":
        if arg:
            cfg.reasoning_effort = "high" if arg.lower() in ("max", "maximum") else arg
            console.system(f"effort → {cfg.reasoning_effort}")
        else:
            console.system(f"effort = {cfg.reasoning_effort}")
    elif cmd == "/cd":
        if arg:
            path = arg if os.path.isabs(arg) else os.path.join(cfg.active_dir, arg)
            if os.path.isdir(path):
                cfg.active_dir = os.path.abspath(path)
                session.refresh_system()
                console.system(f"active tree → {cfg.active_dir}")
            else:
                console.error(f"no such directory: {path}")
        else:
            console.system(f"active tree = {cfg.active_dir}")
    elif cmd == "/raw":
        console.show_thinking = not console.show_thinking
        console.system(f"reasoning display {'on' if console.show_thinking else 'off'}")
    elif cmd == "/reset":
        session.reset()
        console.system("conversation reset")
    elif cmd == "/tools":
        console.system("tools: " + ", ".join(registry.tool_names()))
        for ln in env.report_lines():
            console.system(ln)
    elif cmd == "/index":
        if "build_index" in registry.tool_names():
            console.spinner("building index")
            res = registry.dispatch(session.ctx, "build_index", {})
            console.stop_spinner()
            console.system((res or "indexed").splitlines()[0])
        else:
            console.error("build_index tool not available")
    else:
        console.error(f"unknown command {cmd}  ({HELP})")
    return True


def run(session, console):
    ep = llm.resolve_endpoint(session.cfg)
    console.banner(ep.label, ep.max_ctx)
    while True:
        try:
            line = input(P.paint("\nmicroagent › ", P.GOLD, bold=True))
        except EOFError:
            break
        except KeyboardInterrupt:
            console.line()
            continue
        if not line.strip():
            continue
        if line.lstrip().startswith("/"):
            if not _handle_command(session, console, line):
                break
            continue
        try:
            session.ask(line)
        except KeyboardInterrupt:
            console.stop_spinner()
            console.error("interrupted")
    console.system("bye")
