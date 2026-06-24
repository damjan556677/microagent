#!/usr/bin/env bash
# Parallel eval matrix: sync once, then run the suite CONCURRENTLY — one process per model —
# so a single round exercises all models / transports (json|sse) / ctx sizes at once. The four
# 800x models are distinct servers (on 10.123.51.179), so there's no contention; one host just
# orchestrates the I/O. Logs aggregate under one run dir, a subdir per model.
#
# Usage: eval/run_matrix.sh [REMOTE] [TREE] [TASKS]
#   env MODELS="8006 8007 8002 8003"  models to fan out (default: all four)
#   env HOSTS="devbox"                host(s); with >1, models spread across them round-robin
#                                     (each host must have TREE and reach the model servers)
set -uo pipefail
REMOTE="${1:-devbox}"
TREE="${2:-/srv/workspace/HM_Kernel/work_code/kernel/hongmeng}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
TASKS="${3:-$HERE/eval/tasks.txt}"
read -ra MODELS <<< "${MODELS:-8006 8007 8002 8003}"
read -ra HOSTS  <<< "${HOSTS:-$REMOTE}"

GROUP="$(date +%Y%m%d-%H%M%S)"
GROUP_DIR="$HERE/eval/runs/$GROUP"
mkdir -p "$GROUP_DIR"

# sync the repo once to each distinct host (before fanning out — avoids concurrent rsync races)
declare -A seen
for h in "${HOSTS[@]}"; do
  [ -n "${seen[$h]:-}" ] && continue; seen[$h]=1
  echo "== sync -> $h:~/microagent =="
  rsync -az --exclude=__pycache__ --exclude='*.pyc' --exclude='eval/runs' "$HERE/" "$h:microagent/"
done

echo "== matrix $GROUP: models [${MODELS[*]}] across hosts [${HOSTS[*]}] =="
pids=()
n=0
for m in "${MODELS[@]}"; do
  h="${HOSTS[$((n % ${#HOSTS[@]}))]}"; n=$((n + 1))
  ( bash "$HERE/eval/_run_one.sh" "$h" "$TREE" "$m" "$TASKS" \
       "microagent-runs/$GROUP-$m" "$GROUP_DIR/$m" ) &
  pids+=($!)
  echo "  launched model $m on $h (pid $!)"
done

fail=0
for p in "${pids[@]}"; do wait "$p" || fail=$((fail + 1)); done
echo "== matrix done ($fail process(es) nonzero) -> $GROUP_DIR =="
echo "analyze: python3 $HERE/eval/analyze.py $GROUP_DIR"
