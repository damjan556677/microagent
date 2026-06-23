# microag

A small, **self-contained** terminal coding agent that drives a large language model through a set
of built-in tools to **navigate, optimize, build, and deploy a Linux kernel** — entirely from the
command line. It speaks to [OpenRouter](https://openrouter.ai) over plain HTTP and ships with tools
for code search, semantic navigation (clangd), cross-compilation, kernel-config editing, and booting
the result under QEMU on a remote host.

> Repo: https://github.com/damjan556677/microagent

---

## Goal

Give an LLM a tight, dependency-free harness in which it can actually *do kernel work*: read and
modify source, find symbols and callers, build an arm64 kernel image, flip Kconfig options, deploy
the image to a QEMU guest, and verify the boot — observing real results at each step. The agent is
meant to run unattended on a real engineering box, so it is **autonomous by default** but **gates
destructive disk operations**.

Two things shaped the design:

1. **Self-contained.** It must run on a stock machine with no `pip install` and no
   `requirements.txt` — only the Python standard library plus packages already present
   (`requests`, `PyYAML`). Anything else would be vendored into the tree, never installed.
2. **Transparent.** A warm-colored TUI streams the model's reasoning, every tool call, and every
   result, so you can watch (and trust) what it's doing.

## Design goals & constraints

- **Stdlib + preinstalled only.** No external pip dependencies, no `requirements.txt`. The
  OpenRouter client is hand-rolled over `requests`; the TUI is hand-rolled ANSI (no `rich`); the
  loop is synchronous (no `anyio`). Forbidden: `litellm`, `rich`, `anyio`.
- **Synchronous core.** One blocking request/loop, plus a single daemon thread that animates the
  spinner/status line. No `asyncio`/`anyio`.
- **Autonomous, with a safety gate.** The agent runs builds, edits, and SSH/QEMU commands without
  asking — *except* raw-disk operations (`dd`/`mkfs`/`parted`/`wipefs` on `/dev/sd*`,`/dev/mmcblk*`),
  which always require explicit confirmation.
- **Warm-color, command-line-driven TUI.** Streaming thinking, tool I/O, and a live spinner.

## Quickstart

```bash
git clone https://github.com/damjan556677/microagent && cd microagent
export OPENROUTER_API_KEY=sk-or-...

python3 microag.py                                   # interactive REPL
python3 microag.py -p "explain how this kernel image is built"   # one-shot, then exit
python3 microag.py --model opus --effort high        # pick model / reasoning effort
python3 microag.py --tree /path/to/linux-src         # point at a different kernel tree
```

Flags: `--model`, `--effort {low|medium|high}`, `--tree PATH`, `--no-thinking`, `--no-color`,
`--config PATH`, `-p/--prompt` (one-shot).

## REPL commands

| Command | Effect |
|---|---|
| `/help` | list commands |
| `/model [alias]` | show or switch the model |
| `/effort [low\|medium\|high]` | show or set reasoning effort |
| `/cd [path]` | show or change the active tree |
| `/index` | build the fast compile-commands index (+ `.clangd`) for the active tree |
| `/raw` | toggle display of the reasoning stream |
| `/tools` | list available tools |
| `/reset` | clear the conversation (keep settings) |
| `/quit` | exit (also `/q`, `/exit`, Ctrl-D) |

## Tools the model can call

| Group | Tools | Notes |
|---|---|---|
| **Code** | `read_file` `write_file` `edit_file` `list_dir` `glob` | exact-match edits; line-numbered reads |
| **Search** | `search` `cscope` `ctags` | `search` uses ripgrep, falls back to `grep` |
| **Semantic nav (clangd)** | `find_symbol` `references` `hover` `outline` | precise, type-aware; needs `compile_commands.json` |
| **Index** | `build_index` | cscope / ctags / `compile_commands.json`; writes a `.clangd` |
| **Shell** | `run` | `bash -c`; destructive disk ops are confirmation-gated |
| **Kernel** | `build_linux` `kconfig` `trace_build` `deploy_qemu` `ssh_exec` `qemu_console` | build / config / inspect / deploy / verify |

Every tool returns a string and never raises into the loop (`tools/registry.py:dispatch`); add a
tool by appending to a module's `TOOLS` list (and `registry._MODULES` for a new module).

## Architecture

```
microag.py        entry: argparse, REPL vs one-shot, UTF-8 stdout
config.yaml       model, ssh target, kernel paths, gating, tui
agent/
  config.py       YAML + env (OPENROUTER_API_KEY) -> typed Config
  events.py       Status · StreamDelta · ToolCall · ToolResult · Nudge · Done
  llm.py          hand-rolled OpenRouter client (streaming SSE), model registry, retry, recovery
  history.py      message-history helpers (stores content + tool_calls only)
  loop.py         synchronous agent loop -> yields the event stream
  session.py      system prompt + conversation; ask(task)
tools/
  spec.py         ToolContext, schema helper, output truncation
  registry.py     name -> (fn, schema); tools_for(); dispatch() (never raises)
  fs · search · nav · shell · codeindex · kernel
tui/
  palette.py      warm ANSI truecolor palette
  render.py       line-buffered markdown + tool I/O + spinner thread
  repl.py         readline input + slash-commands
knowledge/
  linux_pi_build.md   build/deploy reference injected into the system prompt
```

**Data flow:** `agent/llm.py` streams chat-completion SSE → `agent/loop.py` forwards `StreamDelta`
events live and dispatches tool calls → `tui/render.py` renders the normalized event stream. The
loop stores only the assistant's content + tool calls in history (never the reasoning trace).

## Model & reasoning

- Default alias `deepseek` → `deepseek/deepseek-v4-pro` — a reasoning model that **also reliably
  emits structured tool calls** (the plain `deepseek-r1` reasoner narrates but won't call tools, so
  it's a poor agent backend). Swappable aliases include `opus`, `sonnet`, `kimi`, `glm`; the full
  alias→model map lives in `agent/llm.py`.
- Reasoning effort is sent as OpenRouter's unified `reasoning.effort` (`low|medium|high`; `max` maps
  to `high`).
- The SSE stream is force-decoded as UTF-8 (`resp.encoding = "utf-8"`), otherwise box-drawing and
  arrow characters arrive mojibaked (HTTP defaults `text/*` to ISO-8859-1).
- The client recovers tool calls some open models leak as markup instead of structured `tool_calls`.

## Navigation strategy

Fastest-first, because the kernel is huge and indexing is slow:

1. **`search`** (ripgrep) — always works, no index. Best first tool for a definition (`search
   'name('`) or for callers/uses.
2. **clangd** `hover` / `outline` / `references` / `find_symbol` — precise and type-aware. These need
   `compile_commands.json` (build it once with `build_index`); `references`/`find_symbol` also rely
   on clangd's background index, which warms over a few minutes on a full kernel.
3. **`cscope` / `ctags`** — only if their index already exists (building it scans the whole tree and
   is slow).

`build_index` defaults to the fast `compile_commands.json` and writes a `.clangd` that strips
GCC-only flags clang rejects (notably `-mabi=lp64`, which otherwise makes clang produce no AST), so
clangd can parse kernel translation units.

## Build & deploy workflow

- **Build (local):** `build_linux` does an incremental `make Image` by default (preserves `.config`
  and edits), or a full reconfigure via `build-pi4.sh` (regenerates `.config` from defconfig + the
  Pi/eBPF/PMU fragment). Toolchain: `ARCH=arm64`, `CROSS_COMPILE=aarch64-linux-gnu-`.
- **Config:** `kconfig` reads/sets `.config` options via `scripts/config` + `olddefconfig`.
- **Deploy (remote):** `deploy_qemu` scp's the freshly built `Image` to `ssh.host` and launches
  `run-vm-customk.sh` with `-cpu host,pmu=on` (virtual PMU), then waits for the guest and reports
  `uname -r`. `ssh_exec` (host or guest, double-hop) and `qemu_console` verify and measure on target.
- **Inspect:** `trace_build` shows the toolchain and the exact commands Kbuild runs (`make -n`).

## Configuration (`config.yaml`)

| Key | Meaning |
|---|---|
| `model`, `reasoning_effort`, `max_turns`, `nudge`, `temperature` | LLM behaviour |
| `openrouter.api_base` | endpoint (key comes from `$OPENROUTER_API_KEY`, never stored) |
| `linux_src`, `build_script`, `config_fragment`, `run_scripts_dir`, `cross_compile`, `arch` | kernel tree + build |
| `deploy_image_name` | name the Image is given on the remote |
| `ssh.{host,user,port,guest_ssh_port,remote_qemu_dir}` | remote QEMU host (default `rpi4pmu.local`) |
| `autonomy.{gate_disk_flashing,destructive_patterns}` | the disk-flashing safety gate |
| `tui.{show_thinking,spinner_hz}` | display |

Everything is overridable at runtime via flags or slash-commands.

## Efficiency & safety

- **Context discipline.** Every tool result is resent on every later turn, so outputs are size-capped
  (`MAX_OUTPUT` = 8 KB in `tools/spec.py`; `read_file` defaults to 250 lines) and the system prompt
  steers the model toward targeted reads, `grep`, and counts rather than dumping whole files or the
  entire `.config`. This was a major win — see below.
- **Disk gate.** `tools/shell.run` refuses to run commands matching the destructive patterns without
  confirmation; in non-interactive (one-shot) mode such commands are auto-denied.

## How it was built (the plan)

microag reuses *patterns* from an existing LiteLLM/Rich agent (`ebpf-opt4`) — the turn loop, the
normalized event model, the tool registry/dispatch, tool-call-markup recovery, and the warm palette —
but reimplements them dependency-free, and targets the build/deploy flow of a Linux 6.8 Raspberry-Pi
4 tree. Build order: config + events → OpenRouter client → core tools (fs/search/shell) → TUI + entry
→ agent loop → kernel/index/nav tools + knowledge pack → end-to-end verification.

Key product decisions: interactive REPL **and** one-shot (`-p`); autonomous **except** raw-disk
flashing; build locally + deploy/boot over SSH (default host `rpi4pmu.local`, `-cpu host,pmu=on`);
default to a DeepSeek reasoner that tool-calls reliably.

## Optimization results

After the agent was working, it was tuned over five eval-driven iterations (run it → give it a task →
inspect → improve → repeat). Full log in [`EVAL.md`](EVAL.md).

| # | Area | Change | Result |
|---|---|---|---|
| 1 | UX | line-buffered markdown rendering (`**bold**`, `` `code` ``, headings, fences) | renders correctly; UTF-8 intact |
| 2 | Efficiency | cap tool-output size (`MAX_OUTPUT` 40 K→8 K, `read_file` 2000→250) + prompt guidance | open-ended canary **242,900 → 32,571 tokens (~7.5×)**, ~3.7× cheaper |
| 3 | Navigation | fastest-first guidance; `build_index` default → compile_commands | no more slow index stalls; finds cross-file callers |
| 4 | Correctness | only *mutating* `kconfig` ops mark "edited" | no false "rebuild" nudge / wasted build |
| 5 | Regression | full build→deploy→verify on real hardware | green; stdlib-only intact |

## Status & verification

Verified end-to-end on real hardware: the agent builds the kernel, deploys it via `deploy_qemu` to
`rpi4pmu.local`, and the guest boots `6.8.0-ai4pi` with the hardware PMU exposed (`armv8_pmuv3`, 7
counters). Dependency check passes — the only third-party imports are `requests` and `PyYAML`; no
`requirements.txt`, no `litellm`/`rich`/`anyio`.

## Backlog

- Conversation/history compaction for very long sessions.
- Batch multi-option `kconfig get` to cut chattiness.
- clangd cold background-index warmup is slow on a full kernel (`search` covers it meanwhile).
