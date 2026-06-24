#!/usr/bin/env bash
# Run the task suite once for ONE model on ONE remote host. Does NOT sync — the caller
# (run_remote.sh / run_matrix.sh) syncs the repo to the host first. Safe to run many of
# these concurrently: each uses a distinct remote + local run dir.
#
# Args: REMOTE TREE MODEL TASKS REMOTE_RUN LOCAL_RUN
set -uo pipefail
REMOTE="$1"; TREE="$2"; MODEL="$3"; TASKS="$4"; REMOTE_RUN="$5"; LOCAL_RUN="$6"

mkdir -p "$LOCAL_RUN"
printf 'remote=%s\ntree=%s\nmodel=%s\n' "$REMOTE" "$TREE" "$MODEL" > "$LOCAL_RUN/meta.txt"
ssh -n "$REMOTE" "mkdir -p ~/$REMOTE_RUN"

i=0
while IFS= read -r task || [ -n "$task" ]; do
  case "$task" in ''|\#*) continue ;; esac
  i=$((i + 1))
  printf '%s\n' "$task" > "$LOCAL_RUN/task-$i.txt"
  # base64 the task to dodge shell-quoting across the ssh hop (see tools/kernel.py).
  b64="$(printf '%s' "$task" | base64 | tr -d '\n')"
  ssh -n "$REMOTE" "cd '$TREE' && T=\"\$(printf '%s' '$b64' | base64 -d)\" && \
      python3 ~/microagent/microagent.py -p \"\$T\" --model '$MODEL' \
      --no-color --no-thinking --log ~/$REMOTE_RUN/task-$i.jsonl" \
      > "$LOCAL_RUN/task-$i.out" 2>&1 || echo "  [$MODEL] task $i exit non-zero (see $LOCAL_RUN/task-$i.out)"
done < "$TASKS"

rsync -az "$REMOTE:$REMOTE_RUN/" "$LOCAL_RUN/"
echo "  [$MODEL] done -> $LOCAL_RUN ($i tasks)"
