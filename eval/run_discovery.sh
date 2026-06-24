#!/usr/bin/env bash
# Scaled discovery: run N tasks CONCURRENTLY, each as its OWN job on a round-robin model — a fleet
# of distinct tasks at once. One subdir per task; main then spawns one analysis subagent per task.
#
# Usage: eval/run_discovery.sh [REMOTE] [TREE] [TASKS_FILE]
#   env MODELS="8006 8007 8002 8003"   models to round-robin across the tasks
set -uo pipefail
REMOTE="${1:-devbox}"
TREE="${2:-/srv/workspace/HM_Kernel/work_code/kernel/hongmeng}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
TASKS="${3:-$HERE/eval/tasks-discovery8.txt}"
read -ra MODELS <<< "${MODELS:-8006 8007 8002 8003}"

GROUP="$(date +%Y%m%d-%H%M%S)"
GROUP_DIR="$HERE/eval/runs/disc-$GROUP"
mkdir -p "$GROUP_DIR"
mapfile -t POOL < <(grep -vE '^[[:space:]]*(#|$)' "$TASKS")

echo "== sync -> $REMOTE:~/microagent =="
rsync -az --exclude=__pycache__ --exclude='*.pyc' --exclude='eval/runs' "$HERE/" "$REMOTE:microagent/"

echo "== discovery $GROUP: ${#POOL[@]} tasks across [${MODELS[*]}] (one job per task) =="
pids=()
for i in "${!POOL[@]}"; do
  task="${POOL[$i]}"
  m="${MODELS[$((i % ${#MODELS[@]}))]}"
  n=$(printf '%02d' $((i + 1)))
  tf="$GROUP_DIR/task-$n.task"; printf '%s\n' "$task" > "$tf"
  echo "  task $n -> model $m"
  ( bash "$HERE/eval/_run_one.sh" "$REMOTE" "$TREE" "$m" "$tf" \
       "microagent-runs/disc-$GROUP/task-$n" "$GROUP_DIR/task-$n" ) &
  pids+=($!)
done

fail=0
for p in "${pids[@]}"; do wait "$p" || fail=$((fail + 1)); done
echo "== discovery done ($fail process(es) nonzero) -> $GROUP_DIR =="
echo "GROUP_DIR=$GROUP_DIR"
