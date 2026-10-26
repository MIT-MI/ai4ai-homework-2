"""Glue between Harbor and the harness. Fixed infrastructure; you do not need to read it.

Harbor runs Terminal-Bench: it starts each task's container, calls HarnessAgent.run(), then runs the task's hidden
tests. HarnessAgent runs the harness on your machine and sends its commands into the container (HarborEnv).
hw2/evaluate.py starts Harbor with this agent.
"""

import asyncio
import os
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from hw2.env import HarborEnv
from hw2.harness_loader import run_harness
from hw2.model import Model
from hw2.trace import Trace, render_markdown


class HarnessAgent(BaseAgent):
    def __init__(self, logs_dir: Path, model_name: str | None = None, harness_dir: str | None = None, **kwargs):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        self.harness_dir = Path(harness_dir or os.environ.get("HW2_HARNESS_DIR", "harness")).resolve()

    @staticmethod
    def name() -> str:
        return "hw2-harness"

    def version(self) -> str | None:
        return "1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    def agent_source(self):
        """What run_harness() runs: the harness folder. A single-file agent can return its Agent class instead."""
        return self.harness_dir

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        trace = Trace(Path(self.logs_dir) / "trace.jsonl")
        model = Model(trace=trace)
        env = HarborEnv(environment, asyncio.get_running_loop(), trace)
        exit_status, error = "AgentTimeout", ""
        try:
            result = await asyncio.to_thread(run_harness, self.agent_source(), instruction, model, env, trace)
            exit_status, error = result["exit_status"], result["error"]
        except asyncio.CancelledError:  # Harbor's agent timeout: stop the worker thread at its next call
            model.cancelled = env.cancelled = True
            error = "stopped by Harbor's agent timeout"
            trace.log_end(exit_status=exit_status, error=error, stats=model.stats())
            (trace.path.parent / "trace.md").write_text(render_markdown(trace.path))
            raise  # Harbor still runs the tests
        finally:  # an exception other than the timeout would make Harbor skip the tests; run_harness catches them
            stats = model.stats()
            context.n_input_tokens = stats.get("tokens_in", 0)
            context.n_cache_tokens = stats.get("tokens_cached", 0)
            context.n_output_tokens = stats.get("tokens_out", 0)
            context.cost_usd = stats.get("cost_usd", 0.0)
            context.metadata = {"exit_status": exit_status, "calls": stats["calls"],
                                "error": (error.strip().splitlines() or [""])[-1][:300]}  # e.g. a harness crash
