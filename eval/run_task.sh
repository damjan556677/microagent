#!/usr/bin/env bash
# Run ONE microagent task for ONE model on the remote and collect its JSONL log. Built for a
# DISCOVERY SUBAGENT to call directly (with an explicit long Bash timeout, e.g. 1800000 ms) so the
# subagent owns its task end-to-end (run + then analyze the log). The task is passed BASE64-encoded
# to avoid all shell-quoting pitfalls across the ssh hop.
#
# Args: MODEL  TASK_B64  LOCAL_DIR  [REMOTE] [TREE]
#   e.g. bash eval/run_task.sh 8006 "$(printf %s 'List the top-level dirs' | base64 -w0)" /tmp/d1
set -uo pipefail
MODEL="$1"; TASK_B64="$2"; LOCAL_DIR="$3"
REMOTE="${4:-devbox}"; TREE="${5:-/srv/workspace/HM_Kernel/work_code/kernel/hongmeng}"

mkdir -p "$LOCAL_DIR"
RR="microagent-runs/$(basename "$LOCAL_DIR")-$$"
ssh -n "$REMOTE" "mkdir -p ~/$RR && cd '$TREE' && T=\"\$(printf %s '$TASK_B64' | base64 -d)\" && \
    python3 ~/microagent/microagent.py -p \"\$T\" --model '$MODEL' --no-color --no-thinking \
    --log ~/$RR/run.jsonl" > "$LOCAL_DIR/run.out" 2>&1 || echo "(microagent exited non-zero — see run.out)"
rsync -az "$REMOTE:$RR/" "$LOCAL_DIR/" 2>/dev/null || true
echo "MODEL=$MODEL  LOG=$LOCAL_DIR/run.jsonl  OUT=$LOCAL_DIR/run.out"
