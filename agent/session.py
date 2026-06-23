"""Session: ties config + conversation history + tools + renderer together, and
exposes `ask(task)` used by both the REPL and one-shot mode.
"""
import os

from . import loop
from tools import registry
from tools.spec import ToolContext

SYSTEM_TMPL = """\
You are **microagent**, an autonomous Linux-kernel engineer working at a terminal. You operate
directly on a kernel source tree: you can read and modify code, search and index it, build it
with a cross-compiler, and deploy/boot the result under QEMU on a remote host to validate
changes.

Active tree: {tree}
Target arch: {arch}    Cross-compiler: {cross}
Remote host that runs QEMU: {ssh}

How to work:
- Think first, then act. Before a tool call, briefly state your goal and what the result will
  tell you; after a result, say what it showed and your next step.
- Navigate fastest-first: `search` (ripgrep) always works with no index — it's the best first
  tool for a definition (`search 'name('`) or callers/uses. The clangd tools (`hover`, `outline`,
  `references`, `find_symbol`) are precise and type-aware and need `compile_commands.json` (build
  once with build_index); `references`/`find_symbol` also need clangd's background index, which
  warms over a few minutes on a full kernel — so early on prefer `search` for cross-tree results.
  Use `cscope`/`ctags` only if their index is already built (building it is slow on a full kernel).
  Read code before editing it.
- Make minimal, correct, well-justified changes. Prefer config-level changes (kconfig) when
  they suffice; patch source only when necessary. Keep edits surgical (edit_file with exact text).
- ALWAYS rebuild after changing source/config (build_linux or `make`), and verify a kernel
  change by deploying and booting it (deploy_qemu) then checking the target (ssh_exec
  `uname -r`, read the console). Report before/after for any optimization.
- Use `run` for arbitrary shell (git, make targets, perf, objdump, readelf). Destructive disk
  commands (dd/mkfs/parted on /dev/sd*,/dev/mmcblk*) are gated and need human confirmation —
  avoid them unless explicitly asked.
- Be economical with context: every tool result is resent on every later turn, so keep outputs
  small. Read targeted line ranges (not whole files), and prefer search/grep, `cscope`, the nav
  tools, `kconfig get`, or counting commands (`grep -c ... .config`) over dumping whole files or
  the entire `.config`.
- Be concise in narration; let tool results speak. Stop when the task is genuinely complete.
"""


def build_system_prompt(cfg) -> str:
    base = SYSTEM_TMPL.format(tree=cfg.active_dir, arch=cfg.arch,
                             cross=cfg.cross_compile, ssh=cfg.ssh.target)
    kp = cfg.knowledge_pack_path
    if os.path.exists(kp):
        try:
            base += "\n\n# Build/deploy reference (this tree)\n" + open(kp).read()
        except Exception:
            pass
    return base


class Session:
    def __init__(self, cfg, console, confirm=None):
        self.cfg = cfg
        self.console = console
        self.ctx = ToolContext(cfg=cfg, confirm=confirm)
        self.messages = [{"role": "system", "content": build_system_prompt(cfg)}]

    def refresh_system(self):
        self.messages[0] = {"role": "system", "content": build_system_prompt(self.cfg)}

    def reset(self):
        self.messages = [{"role": "system", "content": build_system_prompt(self.cfg)}]

    def ask(self, task: str):
        """Run one task to completion, rendering events as they stream."""
        self.messages.append({"role": "user", "content": task})
        counters: dict = {}
        for ev in loop.run(self.cfg, self.ctx, self.messages,
                           registry.tools_for(), registry.dispatch):
            self.console.render_event(ev, counters)
