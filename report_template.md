# Homework 2: <your name>

## 1. Method

Explain your loop, including what you changed in each step and why.

| step | starting version | yours |
|---|---|---|
| 1. `select_parent()` | The harness with the best search score so far. | |
| 2. `propose()` and `proposer_prompt.md` | Claude Code with Kimi K3 edits a copy of the parent. | |
| 3. `check()` | The child must change something, must not name any task, and its `agent.py` must import. | |

## 2. Scores

| harness | search (11 tasks × 3 attempts) | test (10 tasks × 3 attempts) | cost per attempt |
|---|---|---|---|
| starting harness (c000) | | | |
| your best (cXXX) | | | |

Give each score as mean ± standard error over the three attempts. Paste `archive/summary.md`, and give the total cost
of the evaluations and of the proposer.

## 3. Is it really better?

1. Do you think the improvement generalizes? Compare the gain on the test split with the gain on the search split.
2. How often does a harness get different results on the same task across its three attempts? Is your gain bigger
   than this noise?
3. What changed from the starting harness to your best one?
4. Why does it work? Explain it with the traces of tasks that flipped.

## 4. Observations

How did the proposer work? Did you see score gaming or task-specific code? What would you change in your method?
