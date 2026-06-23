# microag

A small, **stdlib-only** terminal coding agent that drives an LLM (DeepSeek reasoner via
OpenRouter) through built-in tools to **navigate, optimize, build, and deploy a Linux kernel**.
No `pip install`, no `requirements.txt` — only the Python stdlib plus packages already on the
system (`requests`, `PyYAML`).

## Run

```bash
export OPENROUTER_API_KEY=sk-or-...
python3 microag.py                       # interactive REPL
python3 microag.py -p "explain how this kernel image is built"   # one-shot
python3 microag.py --model opus --effort high
python3 microag.py --tree /path/to/linux-src
```

## REPL commands
`/help` `/model [alias]` `/effort [low|medium|high]` `/cd [path]` `/index` `/raw`
`/tools` `/reset` `/quit`

## Tools the model can call
- **Code:** `read_file` `write_file` `edit_file` `list_dir` `glob`
- **Search:** `search` (ripgrep→grep) · `cscope` · `ctags`
- **Semantic nav (clangd):** `find_symbol` `references` `hover` `outline`
- **Index:** `build_index` (cscope + ctags + compile_commands.json; writes a `.clangd`)
- **Shell:** `run` (destructive disk ops are confirmation-gated)
- **Kernel:** `build_linux` · `kconfig` · `trace_build` · `deploy_qemu` · `ssh_exec` · `qemu_console`

## Layout
```
microag.py        entry (REPL / one-shot)
config.yaml       model, ssh target, paths, gating
agent/            config · events · llm (OpenRouter over requests) · history · loop · session
tools/            spec · registry · fs · search · nav(clangd) · shell · codeindex · kernel
tui/              palette (warm ANSI) · render · repl
knowledge/        linux_pi_build.md (build/deploy reference injected into the system prompt)
```

## Defaults & environment
- Model `deepseek` → `deepseek/deepseek-v4-pro` (a reasoner that reliably emits tool calls;
  plain `deepseek-r1` reasons but won't tool-call). Swap via `/model` or `--model`.
- Build is local (`aarch64-linux-gnu-`); QEMU runs on the remote `ssh.host` (default `rpi4pmu`,
  set it to a reachable host) via `run-vm-customk.sh` with `-cpu host,pmu=on`.
- `build_index` writes a `.clangd` into the tree stripping GCC-only flags (e.g. `-mabi=lp64`)
  so clang can parse kernel TUs.
