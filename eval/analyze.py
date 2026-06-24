#!/usr/bin/env python3
"""Summarize microagent eval logs (the JSONL produced by `microagent.py --log`).

Usage: eval/analyze.py [RUN_DIR | file.jsonl ...]   (default: newest eval/runs/*)

Per task it reports tool calls, tool failures, repeated identical calls (loops),
total tokens, context %, the terminal reason, and wall-time — plus the first few
failure messages so you can see WHAT broke. Stdlib only.
"""
import glob
import json
import os
import sys


def _load(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def analyze(path):
    recs = _load(path)
    start = next((r for r in recs if r.get("type") == "task_start"), {})
    summ = next((r for r in recs if r.get("type") == "summary"), {})
    calls = [r for r in recs if r.get("type") == "ToolCall"]
    fails = [r for r in recs if r.get("type") == "ToolResult" and not r.get("ok", True)]
    seen, dups = {}, 0
    for c in calls:
        k = (c.get("name"), json.dumps(c.get("input"), sort_keys=True))
        seen[k] = seen.get(k, 0) + 1
        if seen[k] > 1:
            dups += 1
    return {
        "file": os.path.basename(path),
        "model": (start.get("model", "") or ""),
        "task": (start.get("task", "") or "")[:46],
        "tools": summ.get("tool_calls", len(calls)),
        "fails": summ.get("tool_failures", len(fails)),
        "dups": dups,
        "tok": (summ.get("usage") or {}).get("total_tokens"),
        "ctx_pct": summ.get("ctx_pct"),
        "reason": summ.get("reason"),
        "wall_s": summ.get("wall_s"),
        "fail_msgs": [f"{r.get('name')}: {' '.join((r.get('text') or '').split())[:90]}" for r in fails[:4]],
    }


def main(argv):
    if not argv:
        runs = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs", "*")))
        argv = [runs[-1]] if runs else []
    paths = []
    for a in argv:
        if os.path.isdir(a):                       # recurse so a matrix group dir (model subdirs) works
            paths += sorted(glob.glob(os.path.join(a, "**", "*.jsonl"), recursive=True))
        else:
            paths.append(a)
    if not paths:
        print("no logs found"); return
    hdr = f"{'file':<12}{'model':<22}{'tools':>6}{'fail':>5}{'dup':>5}{'tok':>9}{'ctx%':>6}{'reason':>8}  task"
    print(hdr); print("-" * len(hdr))
    tot_fail = 0
    for p in paths:
        r = analyze(p); tot_fail += r["fails"] or 0
        print(f"{r['file']:<12}{r['model'][:21]:<22}{str(r['tools']):>6}{str(r['fails']):>5}{str(r['dups']):>5}"
              f"{str(r['tok']):>9}{str(r['ctx_pct']):>6}{str(r['reason']):>8}  {r['task']}")
        for m in r["fail_msgs"]:
            print(f"      ✗ {m}")
    print(f"\ntotal tool failures across suite: {tot_fail}")


if __name__ == "__main__":
    main(sys.argv[1:])
