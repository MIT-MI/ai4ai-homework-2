"""The outer loop that evolves the harness. Your method is the three functions marked "yours".

    python evolve.py --run runs/main --rounds 8

Each round runs four steps:

    1. select_parent()  which harness to build on?                      yours
    2. propose()        the proposer writes a child harness             yours
    3. check()          may the child be evaluated?                     yours
    4. evaluate         11 search tasks x 3 attempts -> search score    fixed

The test tasks are never used here. You evaluate your best harness on them once, after the loop (README, section 4).

The TODOs suggest what to try. The rest of the loop is in hw2/loop.py. Every harness is saved in runs/<run>/archive/,
so the same command continues a stopped run.
"""

import argparse
from pathlib import Path

from hw2.loop import Archive, best, fill_workspace, import_error, run_claude_code, task_names_in, unchanged

PROMPT = Path(__file__).parent / "proposer_prompt.md"


def select_parent(archive: Archive) -> dict:
    """1. Pick the harness that this round builds on. archive.all() has every harness's meta.json.

    Starter: the harness with the best search score (greedy hill climbing).
    TODO: e.g. sample from the whole archive, favoring high scores and harnesses with few children (DGM), build only on
    children that beat their parent by more than the noise (RRSI), or let the proposer choose (Meta-Harness).
    m["scores"]["tasks"][task]["attempts"] has the reward of every attempt."""
    return best(archive)


def propose(parent: dict, archive: Archive, ws: Path) -> float:
    """2. Write a child harness to ws/harness (and a ws/proposal.md). Returns what the proposer cost (USD).

    Starter: Claude Code with Kimi K3 edits a copy of the parent's harness, following proposer_prompt.md. It can
    read every earlier harness's code, proposal, scores and traces, but not the tests' output.
    TODO: what the proposer sees (e.g. with_tests_output=True), its instructions, several proposals per round,
    or another agent or model instead of Claude Code."""
    fill_workspace(ws, archive, parent, PROMPT)
    return run_claude_code(ws, log=archive.dir / ws.name / "proposer" / "claude.jsonl")  # ws.name is the child's id


def check(child: Path, parent: Path) -> str:
    """3. Return '' if the child may be evaluated, else the reason why not.

    Starter: the child changes something, names no task (as in Meta-Harness), and its agent.py can be imported.
    TODO: e.g. run the child on one search task first and reject it if it crashes (DGM), or ask a critic model to
    reject changes that only fit these tasks (RRSI)."""
    if unchanged(child, parent):
        return "no change"
    if names := task_names_in(child):
        return "names tasks: " + ", ".join(names)
    if error := import_error(child):
        return "cannot import agent.py: " + error
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--rounds", type=int, default=8)
    args = ap.parse_args()

    archive = Archive(args.run)  # a new run starts with c000, the starting harness, scored on the search split
    for _ in range(args.rounds):
        parent = select_parent(archive)                                 # 1. select parent
        ws = archive.new_child()                                        # an empty workspace, named after the child
        cost = propose(parent, archive, ws)                             # 2. propose
        reason = check(ws / "harness", archive.harness(parent["id"]))   # 3. check
        archive.add(ws.name, ws / "harness", parent=parent["id"], invalid_reason=reason, proposer_cost=cost)
        print(f"{ws.name} <- {parent['id']}: {reason or 'valid'}, proposer ${cost:.2f}")
        if not reason:
            archive.evaluate(ws.name)                                   # 4. evaluate on the search split
    print(f"best: {best(archive)['id']}")


if __name__ == "__main__":
    main()
