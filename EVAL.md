# microag — iterative optimization log

Model under test: `deepseek-v4-pro` (default). Each row: task · observation · change · before→after.

## Iteration 1 — markdown rendering (UX)
- **Task:** render assistant markdown (`**bold**`, `` `code` ``, `#` headings) instead of raw markers.
- **Observation:** model emits markdown; renderer printed it literally (`**bold**` shown as-is).
  Also raw streaming made inline formatting impossible (markers split across token deltas).
- **Change:** `tui/render.py` — added `render_md_line()` and switched assistant text to a
  **line-buffered** path (`stream()`/`_emit_text_line()`); thinking stays char-streamed. `**bold**`
  → ANSI bold, `` `code` `` → amber, `#`/`##` → gold bold, ``` fences passed through verbatim;
  no-color mode strips the markers.
- **Result:** unit + live one-shot confirm correct rendering; UTF-8 box-drawing intact. ✓

## Iteration 2 — context/token efficiency (the 242k canary)
- **Task:** detailed kernel breakdown (dir tree + config summary: #modules, #builtin, key features).
- **Observation:** open-ended exploration blew up — original run **29 tool calls / 242,900 tok /
  $0.058**. Cause: large tool outputs (whole `.config` reads at `read_file` limit 2000, `MAX_OUTPUT`
  40KB) are **resent in full every turn** → ~quadratic context growth.
- **Change:** `tools/spec.py` MAX_OUTPUT 40000→**8000**; `tools/fs.py` read_file default limit
  2000→**250** lines; `agent/session.py` system prompt now tells the model to keep tool outputs
  small (targeted ranges; grep/`cscope`/nav/`kconfig get`/`grep -c` instead of dumping files/.config).
- **Result:** comparable detailed task now **4 tool calls / 32,571 tok / $0.016** — ~7.5× fewer
  tokens, ~3.7× cheaper, full answer quality retained. ✓

## Iteration 3 — navigation defaults (correctness/UX)
- **Task:** "find the definition and all callers of futex_wait_queue and explain it."
- **Observation:** model reached for `cscope` first → index not built → it then ran
  `build_index what=all`, which runs `make cscope`+`tags`+`compile_commands` over the WHOLE kernel
  and blew past the timeout → task stalled with no answer.
- **Change:** `agent/session.py` nav guidance rewritten "fastest-first" (lead with `search`; clangd
  needs only compile_commands.json; cscope/ctags only if already built). `tools/codeindex.py`
  `build_index` default `what` **all→compile_commands** (fast; the slow cscope/tags are opt-in),
  description warns they're slow.
- **Result:** re-run uses `search`+`read_file` (8 calls / 27,913 tok / $0.0096), finds the
  definition AND both call sites (incl. cross-file `requeue.c:823`) with an accurate explanation. ✓

## Iteration 4 — false "rebuild" nudge (correctness)
- **Task:** read-only "is PREEMPT enabled? list 5 PMU config options + state" (kconfig get).
- **Observation:** agent answered correctly, then a **false nudge** fired ("you modified config but
  haven't rebuilt") and it ran an unnecessary full `build_linux`. Cause: `agent/loop.py`
  `_update_state` marked `edited_src` for ANY `kconfig` call — including read-only `kconfig get`.
- **Change:** only mutating kconfig ops (`enable/disable/module/set`) set `edited_src`; `get` is read-only.
- **Result:** re-run answers and stops cleanly — no spurious nudge, no wasted build
  (12 calls / 15,423 tok / $0.0040). ✓

## Iteration 5 — final regression check (full loop)
- **Task:** agent-driven "build → deploy to QEMU → verify guest uname -r + PMU" (real, rpi4pmu.local).
- **Observation:** confirm none of iters 1-4 broke the build/deploy path; deps still stdlib-only.
- **Result:** clean — `deploy_qemu` booted `6.8.0-ai4pi`, guest `dmesg`: "armv8_pmuv3 PMU driver,
  7 counters", sysfs type=10; 3 calls / 19,151 tok / $0.0068. Deps check: only `requests`+`yaml`,
  no requirements.txt, no litellm/rich/anyio; 20 tools import. ✓

## Summary (5 iterations)
- **UX:** markdown now renders (bold/code/headings, fenced code, UTF-8); line-buffered text path.
- **Efficiency:** big tool-output sizing fix (MAX_OUTPUT 40k→8k, read_file 2000→250, prompt
  guidance) → the open-ended "structure" canary dropped ~7.5× in tokens (242k→32k).
- **Navigation:** fastest-first guidance + `build_index` default → compile_commands (no more slow
  cscope/tags stalls); nav tasks complete and find cross-file callers.
- **Correctness:** fixed false "rebuild" nudge on read-only `kconfig get`.
- **No regression:** full build→deploy→verify on real hardware still green; stdlib-only intact.
- **Remaining backlog:** optional history compaction for very long sessions; reduce chattiness on
  multi-option `kconfig get` (batch); clangd background-index warmup is slow on a cold kernel
  (search covers it meanwhile).
