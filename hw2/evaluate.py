"""Score a harness on Terminal-Bench tasks. Fixed infrastructure: you only need its command line.

    python -m hw2.evaluate --harness harness --split splits/test.txt --out runs/seed-test -k 3
    python -m hw2.evaluate --harness harness --tasks nginx-request-logging --out runs/try

It calls Harbor (https://harborframework.com), the benchmark runner of Terminal-Bench: for each task, Harbor starts
the task's container, runs the harness in it (through hw2/harbor_agent.py), then runs the task's hidden tests.
Writes, under --out:
    scores.json                accuracy, n_passed, n_tasks, cost_usd, and one entry per task
    tasks/<task>/trace.md      what the model saw and did; also trace.jsonl, tests.txt (the tests' output), result.json
    jobs/                      Harbor's own job folder (raw logs)
"""

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from hw2.model import check_api_key

DATASET = "terminal-bench@2.0"


def read_split(path) -> list[str]:
    """One task name per line; `#` starts a comment."""
    return [line.split("#")[0].strip() for line in Path(path).read_text().splitlines() if line.split("#")[0].strip()]


def evaluate(harness_dir, tasks: list[str], out_dir, *, n_concurrent=8, attempts=1, job_name=None) -> dict:
    check_api_key()  # fail here rather than in every task
    harness_dir, out_dir = Path(harness_dir).resolve(), Path(out_dir)
    job_name = job_name or f"{out_dir.name}-{time.strftime('%Y%m%d-%H%M%S')}"
    cmd = ["harbor", "run", "-d", DATASET, "-a", "hw2.harbor_agent:HarnessAgent", "--ak", f"harness_dir={harness_dir}",
           "-e", "docker", "-n", str(n_concurrent), "-k", str(attempts), "-o", str(out_dir / "jobs"),
           "--job-name", job_name, "-y",
           # retry a task whose container failed to start (e.g. a network error while pulling its image)
           "-r", "2", "--retry-include", "RuntimeError", "--retry-include", "EnvironmentStartTimeoutError"]
    for task in tasks:
        cmd += ["-i", task]
    env = os.environ | {"PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
    subprocess.run(cmd, env=env, check=True)
    return collect(out_dir / "jobs" / job_name, out_dir, tasks)


INFRA_ERRORS = ("RuntimeError", "EnvironmentStartTimeoutError")  # the task's container never started


def collect(job_dir: Path, out_dir: Path, tasks: list[str]) -> dict:
    """Harbor's job folder -> out_dir/tasks/<task>/ and out_dir/scores.json. A task's reward is the mean over its
    attempts; its files come from its first attempt. Attempts whose container never started (e.g. a network error
    while pulling the image) say nothing about the harness: they are left out of the score and listed in `not_run`."""
    runs = {}
    for result_path in sorted(job_dir.glob("*/result.json")):
        r = json.loads(result_path.read_text())
        meta = (r.get("agent_result") or {}).get("metadata") or {}
        exc = r.get("exception_info") or {}
        attempt = {"reward": float(((r.get("verifier_result") or {}).get("rewards") or {}).get("reward", 0.0)),
                   "exit_status": meta.get("exit_status", ""), "calls": meta.get("calls", 0),
                   "cost_usd": (r.get("agent_result") or {}).get("cost_usd") or 0.0,
                   "error": meta.get("error") or (f"{exc.get('exception_type', '')}: "
                                                  f"{exc.get('exception_message', '')[:300]}" if exc else "")}
        if exc.get("exception_type") in INFRA_ERRORS and not r.get("verifier_result") and not attempt["calls"]:
            continue
        runs.setdefault(r["task_name"].split("/")[-1], []).append((result_path.parent, attempt))
    per_task = {}
    for task, attempts in sorted(runs.items()):
        trial_dir, first = attempts[0]
        dst = out_dir / "tasks" / task
        shutil.rmtree(dst, ignore_errors=True)
        dst.mkdir(parents=True)
        for name in ("trace.md", "trace.jsonl"):
            if (trial_dir / "agent" / name).exists():
                shutil.copy(trial_dir / "agent" / name, dst / name)
        tests = [(trial_dir / "verifier" / f).read_text(errors="replace")
                 for f in ("test-stdout.txt", "test-stderr.txt") if (trial_dir / "verifier" / f).exists()]
        (dst / "tests.txt").write_text("\n".join(tests))
        (dst / "result.json").write_text(json.dumps(first, indent=2))
        per_task[task] = {"reward": sum(a["reward"] for _, a in attempts) / len(attempts),
                          "cost_usd": sum(a["cost_usd"] for _, a in attempts), "attempts": [a for _, a in attempts]}
    scores = {
        "accuracy": round(sum(t["reward"] for t in per_task.values()) / max(1, len(per_task)), 4),
        "n_passed": sum(t["reward"] >= 1.0 for t in per_task.values()),
        "n_tasks": len(per_task),
        "cost_usd": round(sum(t["cost_usd"] for t in per_task.values()), 4),
        "not_run": sorted(set(tasks) - set(per_task)),
        "tasks": per_task,
    }
    (out_dir / "scores.json").write_text(json.dumps(scores, indent=2))
    return scores


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--harness", type=Path, default=Path("harness"))
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--split", type=Path, help="file with one task name per line")
    group.add_argument("--tasks", nargs="+", help="task names")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("-n", "--n-concurrent", type=int, default=8)
    ap.add_argument("-k", "--attempts", type=int, default=1)
    args = ap.parse_args()
    tasks = read_split(args.split) if args.split else args.tasks
    s = evaluate(args.harness, tasks, args.out, n_concurrent=args.n_concurrent, attempts=args.attempts)
    for task, t in s["tasks"].items():
        a = t["attempts"][0]
        print(f"{t['reward']:.2f}  {task:40s} {a['exit_status']:15s} {a['calls']:3d} calls  ${t['cost_usd']:.3f}  {a['error'][:60]}")
    print(f"\naccuracy {s['accuracy']:.3f} ({s['n_passed']}/{s['n_tasks']}), cost ${s['cost_usd']:.2f}. "
          f"Traces: {args.out / 'tasks'}/<task>/trace.md")
    if s["not_run"]:
        print(f"WARNING: no container started for {', '.join(s['not_run'])} (network or Docker), "
              f"so they are left out of the score.")


if __name__ == "__main__":
    main()
