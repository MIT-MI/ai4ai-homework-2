"""The plumbing of evolve.py: the archive, workspaces, Claude Code, checks and evaluation.

Fixed infrastructure. You only need the functions that evolve.py imports; their docstrings say what they do.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from hw2.evaluate import evaluate, read_split
from hw2.model import MODEL, PRICES

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "harness"
SEARCH_TASKS = read_split(ROOT / "splits" / "search.txt")
TEST_TASKS = read_split(ROOT / "splits" / "test.txt")  # only used to check that no task name leaks into a harness
ATTEMPTS = 3  # per search task; the score is the mean reward
N_CONCURRENT = 8  # attempts evaluated in parallel
PROPOSER_TIMEOUT = 45 * 60  # seconds per Claude Code session
# Claude Code's model. kimi-k3 runs it on Kimi with your Moonshot key; a Claude model (e.g. opus) uses your Claude account.
PROPOSER_MODEL = os.getenv("HW2_PROPOSER_MODEL", "kimi-k3")


class Archive:
    """runs/<run>/archive/<id>/ holds harness/, meta.json, proposal.md, proposer/ (logs), scores.json and
    tasks/<task>/ (traces). A new run starts with c000, the seed, scored on the search split."""

    def __init__(self, run_dir: Path):
        self.run, self.dir = Path(run_dir), Path(run_dir) / "archive"
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self.all():
            self.add("c000", SEED, parent=None, invalid_reason="")
            (self.dir / "c000" / "proposal.md").write_text("The seed: mini-swe-agent v1 with its bare-minimum prompts.\n")
        for m in self.all():  # including a child whose evaluation was interrupted
            if m["valid"] and not m.get("scores"):
                self.evaluate(m["id"])

    def all(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted(self.dir.glob("c*/meta.json"))]

    def get(self, cid: str) -> dict:
        return json.loads((self.dir / cid / "meta.json").read_text())

    def harness(self, cid: str) -> Path:
        return self.dir / cid / "harness"

    def new_child(self) -> Path:
        """An empty workspace for the next child's proposer, named after the child: runs/<run>/workspaces/<id>/."""
        cid = f"c{len(self.all()):03d}"
        ws = self.run / "workspaces" / cid
        shutil.rmtree(ws, ignore_errors=True)
        ws.mkdir(parents=True)
        return ws

    def add(self, cid: str, harness_dir: Path, **meta):
        """Save a candidate: its harness, the proposal.md next to it (if any), and meta.json."""
        shutil.copytree(harness_dir, self.dir / cid / "harness", ignore=shutil.ignore_patterns("__pycache__"),
                        dirs_exist_ok=True)
        if (harness_dir.parent / "proposal.md").exists():
            shutil.copy(harness_dir.parent / "proposal.md", self.dir / cid / "proposal.md")
        self.update(cid, id=cid, valid=not meta.get("invalid_reason"), **meta)

    def update(self, cid: str, **fields):
        path = self.dir / cid / "meta.json"
        old = json.loads(path.read_text()) if path.exists() else {}
        path.write_text(json.dumps(old | fields, indent=2))
        self.write_summary()

    def evaluate(self, cid: str):
        """Score a candidate on the search split; its traces go to archive/<id>/tasks/."""
        print(f"[{cid}] evaluating on {len(SEARCH_TASKS)} search tasks x {ATTEMPTS} attempts ...", flush=True)
        s = evaluate(self.harness(cid), SEARCH_TASKS, self.dir / cid, n_concurrent=N_CONCURRENT,
                     attempts=ATTEMPTS, job_name=f"{self.run.name}-{cid}")
        self.update(cid, scores=s)
        print(f"[{cid}] search score {s['accuracy']:.3f}, ${s['cost_usd']:.2f}; spent ${self.spent():.2f}", flush=True)

    def spent(self) -> float:
        """Kimi spending so far: evaluations and proposals."""
        return sum((m.get("scores") or {}).get("cost_usd", 0) + m.get("proposer_cost", 0) for m in self.all())

    def write_summary(self):
        rows = ["| id | parent | valid | search score | proposal |", "|---|---|---|---|---|"]
        for m in self.all():
            proposal = self.dir / m["id"] / "proposal.md"
            first = proposal.read_text().strip().splitlines()[0][:80].replace("|", "/") if proposal.exists() else ""
            valid = "yes" if m["valid"] else "no: " + m.get("invalid_reason", "")[:40]
            score = f"{m['scores']['accuracy']:.3f}" if m.get("scores") else "-"
            rows.append(f"| {m['id']} | {m.get('parent') or '-'} | {valid} | {score} | {first} |")
        (self.dir / "summary.md").write_text("# Archive\n\nSearch score = mean reward over the search tasks and their "
                                             "attempts.\n\n" + "\n".join(rows) + "\n")


def best(archive: Archive) -> dict:
    """The harness with the highest search score (ties: the newest). This is the one you test at the end."""
    return max((m for m in archive.all() if m["valid"] and m.get("scores")),
               key=lambda m: (m["scores"]["accuracy"], m["id"]))


def fill_workspace(ws: Path, archive: Archive, parent: dict, prompt_file: Path, with_tests_output: bool = False):
    """ws/harness: a copy of the parent's harness, which the proposer edits. ws/archive: every candidate's harness,
    proposal, scores and traces (and the tests' output if with_tests_output). ws/hw2: the fixed code, for reference.
    ws/TASK.md: the proposer's instructions, from prompt_file."""
    shutil.copytree(archive.harness(parent["id"]), ws / "harness", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "hw2", ws / "hw2", ignore=shutil.ignore_patterns("__pycache__"))
    hidden = ["trace.jsonl", "jobs", "proposer", "__pycache__"] + ([] if with_tests_output else ["tests.txt"])
    shutil.copytree(archive.dir, ws / "archive", ignore=shutil.ignore_patterns(*hidden))
    prompt = Path(prompt_file).read_text()
    for key, value in dict(model=MODEL, parent=parent["id"]).items():
        prompt = prompt.replace("{" + key + "}", str(value))
    (ws / "TASK.md").write_text(prompt)


def run_claude_code(ws: Path, log: Path) -> float:
    """One Claude Code session in ws that follows ws/TASK.md, with the same tools as the Meta-Harness proposer.
    Returns its cost in USD."""
    task = "Follow the instructions in TASK.md (they are repeated here).\n\n" + (ws / "TASK.md").read_text()
    cmd = ["claude", "-p", task, "--safe-mode",  # --safe-mode: ignore your CLAUDE.md, skills, plugins and hooks
           "--permission-mode", "acceptEdits", "--output-format", "stream-json", "--verbose", "--model", PROPOSER_MODEL,
           "--allowedTools", "Read,Glob,Grep,Agent,Write,Edit,Bash"]
    env = os.environ | {"PYTHONPATH": str(ws)}
    if PROPOSER_MODEL.startswith("kimi"):  # through Moonshot's Anthropic-compatible API, subagents included
        env = {k: v for k, v in env.items() if k != "ANTHROPIC_API_KEY"} | {
            "ANTHROPIC_BASE_URL": "https://api.moonshot.ai/anthropic", "ANTHROPIC_AUTH_TOKEN": os.environ["MOONSHOT_API_KEY"],
            "ANTHROPIC_DEFAULT_OPUS_MODEL": PROPOSER_MODEL, "ANTHROPIC_DEFAULT_SONNET_MODEL": PROPOSER_MODEL,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": PROPOSER_MODEL, "CLAUDE_CODE_SUBAGENT_MODEL": PROPOSER_MODEL}
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log.open("w") as f:
            subprocess.run(cmd, cwd=ws, env=env, stdout=f, stderr=subprocess.STDOUT, timeout=PROPOSER_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"Claude Code timed out after {PROPOSER_TIMEOUT // 60} minutes", flush=True)
    return claude_code_cost(log)


def claude_code_cost(log: Path) -> float:
    """What a Claude Code session cost on Kimi, from the token counts in its final result event (0 for other models)."""
    cost = 0.0
    for line in log.read_text(errors="ignore").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            cost = 0.0
            for model, u in (event.get("modelUsage") or {}).items():
                miss, hit, out = PRICES.get(model, (0.0, 0.0, 0.0))
                cost += ((u.get("inputTokens", 0) + u.get("cacheCreationInputTokens", 0)) * miss
                         + u.get("cacheReadInputTokens", 0) * hit + u.get("outputTokens", 0) * out) / 1e6
    return cost


def unchanged(child: Path, parent: Path) -> bool:
    return subprocess.run(["diff", "-rq", "-x", "__pycache__", str(parent), str(child)], capture_output=True).returncode == 0


def task_names_in(harness: Path) -> list[str]:
    """Names of search or test tasks that appear anywhere in the harness's files."""
    code = "\n".join(p.read_text(errors="ignore") for p in harness.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    return [t for t in SEARCH_TASKS + TEST_TASKS if t in code]


def import_error(harness: Path) -> str:
    """'' if harness/agent.py can be imported and defines Agent, else the error (e.g. a syntax error)."""
    r = subprocess.run([sys.executable, "-c", "import sys; from hw2.harness_loader import load_agent_class; "
                        "load_agent_class(sys.argv[1])", str(harness)], capture_output=True, text=True, cwd=ROOT)
    return "" if r.returncode == 0 else (r.stderr.strip().splitlines() or ["error"])[-1][:200]
