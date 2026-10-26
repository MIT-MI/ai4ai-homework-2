"""Where the agent's commands run: the task's container, through Harbor. Fixed infrastructure; the interface that a
harness uses is described in harness/agent.py. Every command is logged to the trace. Like mini-swe-agent, each
command runs in a new `bash -c`, so `cd` and `export` do not carry over.
"""

import asyncio
import time

# mini-swe-agent's environment defaults: no pagers, no progress bars
ENV_VARS = {"PAGER": "cat", "MANPAGER": "cat", "LESS": "-R", "PIP_PROGRESS_BAR": "off", "TQDM_DISABLE": "1"}
MAX_OUTPUT_CHARS = 200_000  # longer outputs are cut in the middle before the harness sees them


class Cancelled(Exception):
    """The trial was stopped from outside (e.g. Harbor's agent timeout); the harness must not run anything else."""


class Env:
    def __init__(self, trace=None):
        self.trace = trace
        self.cancelled = False
        self._template_vars = None

    def execute(self, command: str, timeout: int = 60) -> dict:
        if self.cancelled:
            raise Cancelled()
        start = time.time()
        output = self._execute(command, timeout)
        if len(output["output"]) > MAX_OUTPUT_CHARS:
            s, half = output["output"], MAX_OUTPUT_CHARS // 2
            output["output"] = s[:half] + f"\n[... {len(s) - MAX_OUTPUT_CHARS} characters cut by the environment ...]\n" + s[-half:]
        if self.trace:
            self.trace.log_exec(command, output, time.time() - start)
        return output

    def get_template_vars(self) -> dict:
        if self._template_vars is None:
            lines = self._execute("uname -s; uname -r; uname -v; uname -m", 30)["output"].splitlines()
            self._template_vars = dict(zip(["system", "release", "version", "machine"], (lines + [""] * 4)[:4]))
        return self._template_vars

    def _execute(self, command: str, timeout: int) -> dict:
        """Run `command` and return {"output": stdout+stderr, "returncode": int, "timed_out": bool}."""
        raise NotImplementedError


class HarborEnv(Env):
    """Runs commands in the task's container through Harbor's async environment.exec(). The harness runs in a worker
    thread, so each call is handed to Harbor's event loop and waited for."""

    def __init__(self, environment, loop: asyncio.AbstractEventLoop, trace=None):
        super().__init__(trace)
        self.environment, self.loop = environment, loop
        self._has_timeout = None  # does the container have coreutils' `timeout`?

    def _exec(self, command: str, timeout: int):
        future = asyncio.run_coroutine_threadsafe(
            self.environment.exec(command=command, env=ENV_VARS, timeout_sec=timeout), self.loop)
        return future.result()

    def _execute(self, command, timeout):
        try:
            if self._has_timeout is None:
                self._has_timeout = self._exec("command -v timeout", 30).return_code == 0
            # Harbor returns no output when its own timeout fires, so the container's `timeout` enforces the limit
            # (exit code 124, partial output kept) and Harbor's timeout, 30 s later, is only a backstop.
            if self._has_timeout:
                command = f"timeout -k 5 {int(timeout)} bash -c {_quote(command)}"
            r = self._exec(command, int(timeout) + 30)
        except Exception as e:  # Harbor's backstop timeout, or the container is gone
            return {"output": f"{type(e).__name__}: {e}", "returncode": -1, "timed_out": True}
        output = (r.stdout or "") + (r.stderr or "")
        return {"output": output, "returncode": r.return_code, "timed_out": self._has_timeout and r.return_code == 124}


def _quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"
