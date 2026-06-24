# microagent — discovery findings backlog

Evidence-backed weaknesses found by eval discovery runs. Drawn into Round-N fix batches by
severity / # models affected. Status: `open` → `fixed` (commit) / `wontfix`.

## Run: disc-20260624-124237 (8 tasks, models 8006/8007/8002/8003, orient-first state)
Outcome: 6/8 answered correctly & efficiently; **task-08 (clangd nav) hit `max_turns` with no answer**
and surfaced most of the high-severity items. task-07 (file counts, 4 calls) is the positive ideal.

### HIGH
| id | category | title | evidence | proposed fix |
|---|---|---|---|---|
| F1 | missing-capability | clangd client never reloads a CDB created mid-session → permanent "no symbols" | t08: `clangd --check`=0 errors (call 263) but `outline`/`hover` still "no symbols"; `nav.py:152-167` caches client per-root, `open()` early-returns on `_opened` (123-124); no reload hook | on `build_index` success / new compile_commands.json, respawn the cached clangd client (or send didChangeConfiguration + re-didOpen) |
| F2 | missing-capability | `build_index` is dead on non-Kbuild trees (CMake/Yocto) — hard-requires a Makefile, no fallback | t08 call 27 `build_index` → "(error: …no Makefile — not a kernel tree?)"; `codeindex.py:71` | when no Makefile, generate CDB via `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` / `bear` / `intercept-build`, or emit a minimal CDB |
| F3 | safety | agent MUTATED the tree to force tooling on a read-only task (synthesized compile_commands.json) | t08: call 66 `write_file compile_commands.json` + 11× `edit_file` on it | prompt rule "never synthesize compile_commands.json; if build_index fails, fall back to search"; consider gating write/edit in read-only/eval mode |
| F4 | correctness | ignored the explicit "fall back to search and say so" and emitted NO final answer (dangling tool result, max_turns) | t08 ends call 316 on a tool result, reason=max_turns | prompt rule: after 1 failed semantic-nav setup, stop and answer via `search` + a one-line "clangd unavailable" note |
| F5 | efficiency | runaway loop — ~14 identical edit→re-check→next-missing-header cycles, never converges, no give-up | t08 calls 76-253 | detect ≥3 near-identical failing cycles (same tool, same error class) → force strategy switch / abort to answer |
| F6 | efficiency | max_turns ends with a dangling tool result (no answer); 67% of ctx unused | t08 reason=max_turns at 65 calls, ctx 33% | on max_turns, inject a final "summarize findings and answer now" turn before terminating |

### MED
| id | category | title | evidence | proposed fix |
|---|---|---|---|---|
| F7 | tool-misuse | `glob` brace patterns `{c,h}` silently return 0 (Python glob has no brace-expansion) | t05 calls 47/115/123 `**/*.{c,h}` → 0 despite .c/.h existing; `tools/fs.py:102` | expand `{a,b}` alternations before `glob.glob`, or document/validate in the tool schema |
| F8 | tool-misuse | bad `path` to `search` returns ok=true with a "(search failed via rg: No such file)" body → counted as success | t02 calls 3/4 (`include`, `mm`) | validate `path` exists (or return ok=false) so bad-path calls register as failures |
| F9 | efficiency | unscoped `**/…` globs return 10k–24k-match firehoses that bloat context, add no signal | t01 `**/Makefile`=24240; t05 `**/*mm*`=24325, `**/*page*`=9558 | prompt: scope globs to the active subtree; tool: cap + warn on huge match counts |
| F10 | tool-misuse | `read_file` on a directory wastes a call (error → re-do as list_dir) — recurs across tasks | t01 call ~21, t05 calls 61/69 (".. is a directory; use list_dir") | have `read_file` auto-fall-back to a directory listing instead of erroring |
| F11 | tool-misuse | nav tools return bare "no symbols"/"no hover info" with no cause or fallback hint | t08 calls 24/35/258 | when a TU has 0 symbols, report likely cause (no CDB / preamble errors) + suggest `search` |
| F12 | efficiency | verbose `run` output (clangd --check cc1 lines, find dumps) re-sent every turn | t08 calls 78/143 multi-KB dumps; 968K prompt tok for 27K ctx | summarize/cap `run` output before it enters history |
| F13 | efficiency | token cost is quadratic context re-send with NO prompt caching on local models | t03 520K & t06 319K prompt tok == sum of per-turn ctx; final ctx only 18%/13% | enable prompt caching on the local client (or stop billing the cached prefix in the eval metric) |
| F14 | efficiency | mainline-Linux path reflex (`include/`, `mm/`, `kernel/sched`, `arch/aarch64/...`) probed before/despite orienting | t02 calls 3/4, t04 call 2 (`kernel/sched`→not a dir), t01 | strengthen orient-first: derive next paths ONLY from observed `list_dir` entries (Round-2 stanza) |

### LOW
| id | category | title | evidence | fix |
|---|---|---|---|---|
| F15 | efficiency | over-anchored regexes (`^void \*kmalloc\(`, `^static inline …`) → guaranteed-empty first searches | t02 calls 1/2, t03 call 38 | prompt: start with a bare unanchored substring, then refine |
| F16 | tool-misuse | `edit_file` brittle on whitespace/stale text ("old text not found") | t08 call 238 | optional: whitespace-tolerant matching in edit_file |

### POSITIVE (encode in the system prompt)
- **Batch independent read-only calls in one turn** — t07 did `list_dir` + two `find|wc -l` in a single
  turn (4 calls total, 11K tok); cuts the turn-count multiplier that drives token cost.
- **`find … | wc -l` + `for d in */ … sort -rn | head`** is the right idiom for "how many / which dir".
- orient-first `list_dir` root and citation discipline (t04: all file:line verified) are working.

## Status — Round-2 batch applied & validated (commits aea8c6a, 4b99a6f, f9c34dd)
F1–F12, F14, F15 → **fixed**. Re-ran the same 8 discovery tasks on the patched code (disc-20260624-131639):
tool calls **253→194 (−23%)**, failures **6→1 (−83%)**, tokens **~2.44M→~1.01M (−59%)**, `max_turns`
deaths **1→0**; all tasks end `stop`. The clangd-nav disaster (t08): **65→11 calls, 978K→40K tok,
no-answer → answered.** F16 (edit_file whitespace tolerance) → open (low). 
**F13 (#28) → investigated:** prompt_tokens == sum of per-turn context (no caching); vLLM prefix
caching is server-side and wouldn't change the reported token count, so there's no client-side fix —
the real lever (fewer turns) is done via the loop guardrails. Closing as not-actionable-in-client.

## Run: disc-20260624-132631 (6 HARD tasks) — Round-2 fixes HELD; new findings
Validated under load: all 6 deep tasks ended `stop`, NO max_turns/give-up loops; answers verified
CORRECT & grounded (citations cross-checked vs actual reads); task-06 found a REAL double-free in
`udk_virtblk_probe` (refcnt callback re-frees tag_set/vqs already freed on the probe error path).
- **G1 (correctness, HIGH):** hallucinated/synthesized citations on deep+truncated reads — task-05
  quoted freelists.c "buddy" code it never paged to (read truncated at 100 lines); task-01/03
  asserted inferences as fact. → prompt: cite only lines actually read; page truncated reads before
  quoting; mark inferences. [task #29]
- **G2 (tool-misuse, MED, recurring):** `glob` rejects `path=` kwarg (R3 t02/t03, R1 t08). → add
  `path` to glob_files. [task #30]
- **G3 (efficiency, MED):** header-first search thrashing — t02 13/39 searches empty (`*.h` for
  symbols in `.c`); ~11-call wrong-tree detour. → prompt: symbol-def search no-glob/.c first.
- **G4 (tool-misuse, LOW):** `search` errors when `path` is a FILE → degrade to single-file grep.
- **G5 (efficiency, LOW):** 8003/82K tightest ctx margin (42.6% peak; ~103 calls would hit a guard).
- **G6 (tooling, LOW):** analyze.py should surface peak ctx% + max single Ctx delta from Ctx events.
- POSITIVE: deep traces converged & were grounded; bug-hunt sound; Round-2 guardrails held.

## Run: build-r1 (subagent-driven, single BUILD task, 8006) — microagent BUILT the kernel ✓
microagent ran `qemu_overlay_proc_enabled/build_qemu_image.sh` and produced a verified
`bootimage.elf` (10.5 MB, sha256) in 8 calls / 65K tok — set `timeout=1800`, didn't short-circuit
the stale image, verified the fresh artifact. Two HIGH latent bugs (hidden only because the task
pre-hinted the timeout and the verdict happened to survive truncation):
- **B1 (missing-capability, HIGH) → FIXED:** `run` default timeout 600s < real build 895s (an
  unguided build gets KILLED), and `TimeoutExpired` DISCARDED all partial output (opaque). Fix
  (shell.py): default → 1800s; on timeout, capture partial output + "retry with a larger timeout".
- **B2 (tool-misuse, HIGH) → FIXED:** middle-truncation (`MAX_OUTPUT=8000`) could hide a `gcc error`
  in the dropped middle → false "success". Fix (shell.py): `run` output is now tail-biased and
  surfaces error/warning lines from the omitted middle (verified: a buried `gcc: error` is surfaced).
