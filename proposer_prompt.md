# Improve a terminal agent's harness

You are improving a terminal agent's harness, which is everything around its model (`{model}`): the prompts, how
commands and their outputs are shown to the model, the control flow, how the history is kept, and any extra model
calls. In each task the agent gets a Linux container and an instruction. When the agent stops, or when the task's
time limit of 15 or 20 minutes runs out, hidden tests check the container and score the task 1 or 0.

## Your workspace

- `harness/` is the harness you improve, a copy of candidate `{parent}`. Edit it in place. Its entry point is
  `harness/agent.py`, which defines `class Agent(model, env)` with a method `run(task)`. Keep the interface that its
  docstring describes.
- `archive/summary.md` lists every candidate so far with its parent and its score.
- `archive/<id>/` holds one candidate. `harness/` is its code and `proposal.md` says what its proposer changed and why.
  `meta.json` has its parent, and the reason if it was rejected. `scores.json` has the reward of every attempt.
  `tasks/<task>/trace.md` shows everything the model saw and did in the first attempt at a task, and `result.json`
  next to it has the result of that attempt.
- `hw2/` is a copy of the fixed infrastructure, for reference.

## What to do

1. Read `archive/summary.md`. Then read the traces of failed tasks, for the parent and for other candidates, and find
   out why they fail. Common reasons are a wrong approach, commands that time out, replies in the wrong format,
   running out of time, stopping without checking the result, and missing information about the environment.
2. Change the harness to fix a failure pattern that you saw in several tasks. Prefer one clear change over many small
   ones, so that the score shows whether it worked. Do not run any tasks, because the loop evaluates your harness.
   Before you finish, check that `harness/agent.py` still imports
   (`python -c "from hw2.harness_loader import load_agent_class; load_agent_class('harness')"`) and that every config
   key it uses is still in `config.yaml`.
3. Write `proposal.md` in the workspace root. Describe the failure pattern and cite the tasks and steps where you saw
   it. Then describe your change and why you expect it to help.

## Rules

- Work only with the files in this workspace. Edit only files in `harness/`, and write `proposal.md`.
- Do not hard-code anything about specific tasks, such as their names, file paths or answers from the traces. The
  final harness is scored on tasks that are not in the archive.
- Do not write task names anywhere in `harness/`, not even in comments, because the loop rejects such a harness. Cite
  tasks in `proposal.md` instead.
- Do not try to find, read or change the tests that grade a task.
