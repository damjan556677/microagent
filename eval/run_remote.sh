#!/usr/bin/env bash
# Iterative-eval orchestrator (dev tooling — NOT part of the portable microagent runtime).
# Sync local microagent -> remote, run the task suite inside a kernel tree on the remote
# (one fresh one-shot per task, JSONL-logged), then collect the logs back locally.
#
# Usage: eval/run_remote.sh [REMOTE] [TREE] [MODEL] [TASKS_FILE]
#   REMOTE  ssh alias                (default: devbox)
#   TREE    remote kernel tree to cd into (the agent's active_dir defaults to the cwd)
#   MODEL   model selector, or 'rr' to round-robin 8006/8007/8002/8003 across runs (default: rr).
#           Round-robin spans models, transports (json/sse) and ctx sizes so fixes generalize;
#           pass an explicit selector (e.g. 8006) to PIN a model for a clean before/after A/B.
set -euo pipefail

REMOTE="${1:-devbox}"
TREE="${2:-/srv/workspace/HM_Kernel/work_code/kernel/hongmeng}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
TASKS="${4:-$HERE/eval/tasks.txt}"

# Default rotates the model each run (state persisted in eval/.rr_state); explicit arg pins it.
RR_MODELS=(8006 8007 8002 8003)
RR_STATE="$HERE/eval/.rr_state"
MODEL="${3:-rr}"
if [ "$MODEL" = "rr" ] || [ "$MODEL" = "auto" ]; then
  idx=0; [ -f "$RR_STATE" ] && idx="$(cat "$RR_STATE" 2>/dev/null || echo 0)"
  case "$idx" in ''|*[!0-9]*) idx=0 ;; esac
  MODEL="${RR_MODELS[$((idx % ${#RR_MODELS[@]}))]}"
  printf '%s\n' "$(((idx + 1) % ${#RR_MODELS[@]}))" > "$RR_STATE"
  echo "== round-robin: this run uses model $MODEL (cycle 8006/8007/8002/8003) =="
fi

TS="$(date +%Y%m%d-%H%M%S)"
REMOTE_RUN="microagent-runs/$TS"
LOCAL_RUN="$HERE/eval/runs/$TS"
mkdir -p "$LOCAL_RUN"
printf 'remote=%s\ntree=%s\nmodel=%s\nts=%s\n' "$REMOTE" "$TREE" "$MODEL" "$TS" > "$LOCAL_RUN/meta.txt"

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
