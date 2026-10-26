"""Load a harness folder (its agent.py must define `class Agent`) and run it on one task.

Fixed infrastructure. run_harness() is where the Harbor adapter calls a harness, so every run ends the same way: an
exit status, an error (if any), the model's stats, and a trace.md next to the trace.jsonl.
"""

import importlib.util
import sys
import traceback
import uuid
from pathlib import Path

from hw2.env import Cancelled
from hw2.trace import render_markdown


def load_agent_class(harness_dir: str | Path):
    sys.dont_write_bytecode = True  # keep __pycache__ out of harness folders, so that diffs stay clean
    harness_dir = Path(harness_dir).resolve()
    if str(harness_dir) not in sys.path:
        sys.path.insert(0, str(harness_dir))  # so agent.py can import helper modules next to it
    spec = importlib.util.spec_from_file_location(f"harness_{uuid.uuid4().hex[:8]}", harness_dir / "agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Agent


def run_harness(harness, task: str, model, env, trace) -> dict:
    """`harness` is a harness folder, or an Agent class."""
    exit_status, error = "", ""
    try:
        agent_class = harness if isinstance(harness, type) else load_agent_class(harness)
        exit_status = str(agent_class(model, env).run(task))
    except Cancelled:
        exit_status = "Cancelled"
    except Exception:
        exit_status, error = "HarnessError", traceback.format_exc()
    result = {"exit_status": exit_status, "error": error, "stats": model.stats()}
    trace.log_end(**result)
    (trace.path.parent / "trace.md").write_text(render_markdown(trace.path))
    return result
