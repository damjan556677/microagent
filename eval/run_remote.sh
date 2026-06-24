#!/usr/bin/env bash
# Iterative-eval orchestrator (dev tooling — NOT part of the portable microagent runtime).
# Sync local microagent -> remote, run the task suite inside a kernel tree on the remote
# (one fresh one-shot per task, JSONL-logged), then collect the logs back locally.
#
# Usage: eval/run_remote.sh [REMOTE] [TREE] [MODEL] [TASKS_FILE]
#   REMOTE  ssh alias                (default: devbox)
#   TREE    remote kernel tree to cd into (the agent's active_dir defaults to the cwd)
#   MODEL   microagent model selector (default: 8006 — 1M ctx)
set -euo pipefail

REMOTE="${1:-devbox}"
TREE="${2:-/srv/workspace/HM_Kernel/work_code/kernel/hongmeng}"
MODEL="${3:-8006}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
TASKS="${4:-$HERE/eval/tasks.txt}"

TS="$(date +%Y%m%d-%H%M%S)"
REMOTE_RUN="microagent-runs/$TS"
LOCAL_RUN="$HERE/eval/runs/$TS"
mkdir -p "$LOCAL_RUN"

echo "== sync local microagent -> $REMOTE:~/microagent =="
rsync -az --exclude=__pycache__ --exclude='*.pyc' --exclude='eval/runs' "$HERE/" "$REMOTE:microagent/"

echo "== run suite on $REMOTE in $TREE (model $MODEL) =="
ssh -n "$REMOTE" "mkdir -p ~/$REMOTE_RUN"
i=0
while IFS= read -r task || [ -n "$task" ]; do
  case "$task" in ''|\#*) continue ;; esac
  i=$((i + 1))
  printf '  [%02d] %s\n' "$i" "$task"
  printf '%s\n' "$task" > "$LOCAL_RUN/task-$i.txt"
  # base64 the task to dodge all shell-quoting issues across the ssh hop (see tools/kernel.py).
  b64="$(printf '%s' "$task" | base64 | tr -d '\n')"
  ssh -n "$REMOTE" "cd '$TREE' && T=\"\$(printf '%s' '$b64' | base64 -d)\" && \
      python3 ~/microagent/microagent.py -p \"\$T\" --model '$MODEL' \
      --no-color --no-thinking --log ~/$REMOTE_RUN/task-$i.jsonl" \
      > "$LOCAL_RUN/task-$i.out" 2>&1 || echo "    (exit non-zero; see task-$i.out)"
done < "$TASKS"

echo "== collect logs -> $LOCAL_RUN =="
rsync -az "$REMOTE:$REMOTE_RUN/" "$LOCAL_RUN/"
echo "done: $LOCAL_RUN  ($i tasks)"
echo "analyze: python3 $HERE/eval/analyze.py $LOCAL_RUN"
