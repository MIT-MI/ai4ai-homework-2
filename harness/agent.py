"""The seed harness: mini-swe-agent v1.17.5's DefaultAgent (agents/default.py), cut to the essentials. Its prompts,
in config.yaml, are the bare-minimum defaults from the same file.

This folder (harness/) is the part that gets evolved. Its contract with the fixed code in hw2/:

    Agent(model, env).run(task) -> str          returns an exit status, e.g. "Submitted"
    model.query(messages) -> str                the fixed LLM
    env.execute(command, timeout) -> dict       runs `bash -c command` in the task's container and returns
                                                {"output": str, "returncode": int, "timed_out": bool}
    env.get_template_vars() -> dict             {"system", "release", "version", "machine"} of the container

Everything else (prompts, parsing, control flow, context management, extra model calls, helper files) may change.
"""

import re
from pathlib import Path

import yaml
from jinja2 import StrictUndefined, Template

CONFIG = yaml.safe_load((Path(__file__).parent / "config.yaml").read_text())


class NonTerminatingException(Exception):
    """Raised for conditions that can be handled by the agent."""


class FormatError(NonTerminatingException):
    """Raised when the LM's output is not in the expected format."""


class ExecutionTimeoutError(NonTerminatingException):
    """Raised when the action execution timed out."""


class TerminatingException(Exception):
    """Raised for conditions that terminate the agent."""


class Submitted(TerminatingException):
    """Raised when the LM declares that the agent has finished its task."""


class LimitsExceeded(TerminatingException):
    """Raised when the agent has reached its step limit."""


class Agent:
    def __init__(self, model, env, **kwargs):
        self.config = CONFIG | kwargs
        self.model = model
        self.env = env
        self.messages: list[dict] = []
        self.n_steps = 0

    def render_template(self, template: str, **kwargs) -> str:
        template_vars = self.config | self.env.get_template_vars() | kwargs
        return Template(template, undefined=StrictUndefined).render(**template_vars)

    def add_message(self, role: str, content: str):
        self.messages.append({"role": role, "content": content})

    def run(self, task: str) -> str:
        """Run step() until agent is finished. Return the exit status."""
        self.messages = []
        self.add_message("system", self.render_template(self.config["system_template"]))
        self.add_message("user", self.render_template(self.config["instance_template"], task=task))
        while True:
            try:
                self.step()
            except NonTerminatingException as e:
                self.add_message("user", str(e))
            except TerminatingException as e:
                self.add_message("user", str(e))
                return type(e).__name__

    def step(self) -> dict:
        """Query the LM, execute the action, return the observation."""
        return self.get_observation(self.query())

    def query(self) -> str:
        """Query the model and return the response."""
        if 0 < self.config["step_limit"] <= self.n_steps:
            raise LimitsExceeded()
        self.n_steps += 1
        response = self.model.query(self.messages)
        self.add_message("assistant", response)
        return response

    def get_observation(self, response: str) -> dict:
        """Execute the action and add the observation."""
        output = self.execute_action(self.parse_action(response))
        self.add_message("user", self.render_template(self.config["action_observation_template"], output=output))
        return output

    def parse_action(self, response: str) -> str:
        """Parse the action from the message. Returns the action."""
        actions = re.findall(self.config["action_regex"], response, re.DOTALL)
        if len(actions) == 1:
            return actions[0].strip()
        raise FormatError(self.render_template(self.config["format_error_template"], actions=actions))

    def execute_action(self, action: str) -> dict:
        output = self.env.execute(action, timeout=self.config["command_timeout"])
        if output["timed_out"]:
            raise ExecutionTimeoutError(
                self.render_template(self.config["timeout_template"], action=action, output=output["output"])
            )
        self.has_finished(output)
        return output

    def has_finished(self, output: dict):
        """Raises Submitted if the first line of the output is the magic string."""
        lines = output["output"].lstrip().splitlines(keepends=True)
        if lines and lines[0].strip() == "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" and output["returncode"] == 0:
            raise Submitted("".join(lines[1:]))
