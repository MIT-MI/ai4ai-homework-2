"""The LLM. Fixed infrastructure, not part of the harness: every harness uses the same model.

kimi-k2.6 through Moonshot's OpenAI-compatible API. It retries transient errors, counts tokens and cost, and appends
every call to the trace. There is no limit on calls or cost; each task's own time limit is the only limit.
"""

import os
import time

from hw2.env import Cancelled

API_BASE = "https://api.moonshot.ai/v1"
API_KEY_VAR = "MOONSHOT_API_KEY"  # name of the env var that holds the key
MODEL = "kimi-k2.6"
# USD per 1M tokens: (input cache miss, input cache hit, output). kimi-k3 is the proposer's model (hw2/loop.py).
PRICES = {"kimi-k2.6": (0.95, 0.16, 4.00), "kimi-k3": (3.00, 0.30, 15.00)}
# Kimi-specific request fields: thinking off keeps each step short and cheap.
EXTRA_BODY = {"kimi-k2.6": {"thinking": {"type": "disabled"}}}

MAX_TOKENS = 8192  # per reply


def check_api_key():
    if not os.environ.get(API_KEY_VAR):
        raise RuntimeError(f"{API_KEY_VAR} is not set. Put it in .env, then run: set -a; source .env; set +a")


class Model:
    def __init__(self, name=MODEL, *, trace=None):
        self.name, self.trace = name, trace
        self.n_calls, self.cost, self.tokens_in, self.tokens_cached, self.tokens_out = 0, 0.0, 0, 0, 0
        self.cancelled = False  # set from outside when the trial is stopped
        import openai

        check_api_key()
        self._client = openai.OpenAI(api_key=os.environ[API_KEY_VAR], base_url=API_BASE, timeout=600, max_retries=0)

    def query(self, messages: list[dict]) -> str:
        if self.cancelled:
            raise Cancelled()
        self.n_calls += 1
        start = time.time()
        r = self._create([{"role": m["role"], "content": m["content"]} for m in messages])
        content = r.choices[0].message.content or ""
        cost = self._count(r.usage)
        if self.trace:
            self.trace.log_query(messages, content, seconds=time.time() - start, cost=cost,
                                 finish_reason=r.choices[0].finish_reason)
        return content

    def _create(self, messages, attempts=6):
        import openai

        kwargs = dict(model=self.name, messages=messages, max_tokens=MAX_TOKENS)
        if self.name in EXTRA_BODY:
            kwargs["extra_body"] = EXTRA_BODY[self.name]
        for i in range(attempts):
            try:
                return self._client.chat.completions.create(**kwargs)
            except (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError,
                    openai.InternalServerError):
                if i == attempts - 1:
                    raise
                time.sleep(min(60, 4 * 2**i))  # 4, 8, 16, 32, 60 s

    def _count(self, usage) -> float:
        if usage is None:
            return 0.0
        cached = getattr(usage, "cached_tokens", None)  # Kimi reports it here,
        if cached is None and getattr(usage, "prompt_tokens_details", None):  # OpenAI here
            cached = usage.prompt_tokens_details.cached_tokens
        cached = cached or 0
        self.tokens_in += usage.prompt_tokens
        self.tokens_cached += cached
        self.tokens_out += usage.completion_tokens
        miss, hit, out = PRICES.get(self.name, (0.0, 0.0, 0.0))
        cost = ((usage.prompt_tokens - cached) * miss + cached * hit + usage.completion_tokens * out) / 1e6
        self.cost += cost
        return cost

    def stats(self) -> dict:
        return {"model": self.name, "calls": self.n_calls, "cost_usd": round(self.cost, 4),
                "tokens_in": self.tokens_in, "tokens_cached": self.tokens_cached, "tokens_out": self.tokens_out}
