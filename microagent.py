#!/usr/bin/env python3
"""microagent — a stdlib-only Linux-kernel coding agent.

Usage:
    python3 microagent.py                      # interactive REPL
    python3 microagent.py -p "do X"            # one-shot (non-interactive) then exit
    python3 microagent.py --model opus --effort high
    python3 microagent.py --tree /path/to/linux-src

Needs $OPENROUTER_API_KEY in the environment. No pip install, no requirements.txt —
only the Python stdlib plus already-present requests/PyYAML/pexpect/colorama.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Ensure UTF-8 output regardless of locale, so box-drawing/arrows render correctly.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from agent import config, llm                      # noqa: E402
from agent.session import Session                  # noqa: E402
from tui.render import Console                      # noqa: E402
from tui import repl, palette                       # noqa: E402


def main():
    ap = argparse.ArgumentParser(prog="microagent",
                                 description="stdlib-only Linux-kernel coding agent")
    ap.add_argument("-p", "--prompt", help="run a single task then exit (non-interactive)")
    ap.add_argument("--model", help="model alias (deepseek, opus, kimi, glm, or a full id)")
    ap.add_argument("--effort", help="reasoning effort: low | medium | high")
    ap.add_argument("--tree", help="active kernel tree (overrides config linux_src)")
    ap.add_argument("--no-thinking", action="store_true", help="hide the reasoning stream")
    ap.add_argument("--no-color", action="store_true", help="disable ANSI color")
    ap.add_argument("--config", help="path to a config.yaml")
    ap.add_argument("--log", help="write a structured JSONL session log to this path (for eval)")
    a = ap.parse_args()

    cfg = config.load(a.config)
    if a.model:
        cfg.model = a.model
    if a.effort:
        cfg.reasoning_effort = a.effort
    if a.tree:
        cfg.active_dir = os.path.abspath(a.tree)
    if a.no_thinking:
        cfg.show_thinking = False
    if a.no_color:
        palette.set_enabled(False)

    # A key is only required for the chosen endpoint. Internal servers run keyless unless an
    # api_key_env is configured; OpenRouter always needs OPENROUTER_API_KEY.
    is_internal = cfg.internal.resolve((cfg.model or "").strip()) is not None
    ep = llm.resolve_endpoint(cfg, detect=False)
    if not ep.api_key:
        if not is_internal:
            sys.stderr.write("error: OPENROUTER_API_KEY is not set (required for OpenRouter models).\n")
            sys.exit(2)
        if cfg.internal.api_key_env:
            sys.stderr.write(f"error: {cfg.internal.api_key_env} is not set "
                             f"(required by this internal endpoint).\n")
            sys.exit(2)

    console = Console(cfg)
    try:
        if a.prompt:
            # one-shot: non-interactive, so gated destructive commands are auto-denied.
            Session(cfg, console, confirm=None, log_path=a.log).ask(a.prompt)
        else:
            session = Session(cfg, console, confirm=repl.make_confirm(console), log_path=a.log)
            repl.run(session, console)
    finally:
        console.close()


if __name__ == "__main__":
    main()
