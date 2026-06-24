#!/usr/bin/env bash
# Parallel eval matrix: sync once, then run microagent CONCURRENTLY — one process per model.
# The four 800x models are distinct servers (on 10.123.51.179), so there's no contention; one
# host just orchestrates the I/O. Logs aggregate under one run dir, a subdir per model.
#
#   MODE=shard  (default) — DIFFERENT tasks per model (the pool is split across models) for
#                           broad discovery: max distinct tasks per round.
#   MODE=mirror           — SAME tasks on every model, to verify a fix generalizes (A/B/C/D).
#
# Usage: eval/run_matrix.sh [REMOTE] [TREE] [TASKS]
#   env MODE="shard|mirror"           (default shard)
#   env MODELS="8006 8007 8002 8003"  models to fan out (default: all four)
#   env HOSTS="devbox"                host(s); with >1, models spread across them round-robin
#                                     (each host must have TREE and reach the model servers)
set -uo pipefail
REMOTE="${1:-devbox}"
TREE="${2:-/srv/workspace/HM_Kernel/work_code/kernel/hongmeng}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
TASKS="${3:-$HERE/eval/tasks.txt}"
MODE="${MODE:-shard}"
read -ra MODELS <<< "${MODELS:-8006 8007 8002 8003}"
read -ra HOSTS  <<< "${HOSTS:-$REMOTE}"

GROUP="$(date +%Y%m%d-%H%M%S)"
GROUP_DIR="$HERE/eval/runs/$GROUP"
mkdir -p "$GROUP_DIR"

# the task pool, comments/blanks stripped
mapfile -t POOL < <(grep -vE '^[[:space:]]*(#|$)' "$TASKS")
NM=${#MODELS[@]}

# sync the repo once to each distinct host (before fanning out — avoids concurrent rsync races)
declare -A seen
for h in "${HOSTS[@]}"; do
  [ -n "${seen[$h]:-}" ] && continue; seen[$h]=1
  echo "== sync -> $h:~/microagent =="
  rsync -az --exclude=__pycache__ --exclude='*.pyc' --exclude='eval/runs' "$HERE/" "$h:microagent/"
done

echo "== matrix $GROUP: MODE=$MODE, models [${MODELS[*]}] x hosts [${HOSTS[*]}], ${#POOL[@]} tasks =="
pids=()
for i in "${!MODELS[@]}"; do
  m="${MODELS[$i]}"
  h="${HOSTS[$((i % ${#HOSTS[@]}))]}"
  tf="$GROUP_DIR/tasks-$m.txt"
  if [ "$MODE" = "mirror" ]; then
    printf '%s\n' "${POOL[@]}" > "$tf"                       # every model runs the full pool
  else
    : > "$tf"; j=$i                                          # shard: model i gets POOL[i], [i+NM], ...
    while [ "$j" -lt "${#POOL[@]}" ]; do printf '%s\n' "${POOL[$j]}" >> "$tf"; j=$((j + NM)); done
  fi
  echo "  model $m on $h <- $(wc -l < "$tf") task(s)"
  ( bash "$HERE/eval/_run_one.sh" "$h" "$TREE" "$m" "$tf" \
       "microagent-runs/$GROUP-$m" "$GROUP_DIR/$m" ) &
  pids+=($!)
done

fail=0
for p in "${pids[@]}"; do wait "$p" || fail=$((fail + 1)); done
echo "== matrix done ($fail process(es) nonzero) -> $GROUP_DIR =="
echo "analyze: python3 $HERE/eval/analyze.py $GROUP_DIR"
