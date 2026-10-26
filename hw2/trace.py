"""Execution traces. Fixed infrastructure: the model and the environment write every call here, so the trace is the
same whatever the harness does (a harness can hide things from its model, but not from the trace).

trace.jsonl has one event per line:
    {"type": "query", "keep": k, "new": [...], "response": "...", ...}   the input = the first k messages of the
                                        previous input + reply, then `new` (so a harness that rewrites history shows up)
    {"type": "exec", "command": "...", "output": "...", "returncode": 0, "timed_out": false, "seconds": 1.2}
    {"type": "end", "exit_status": "...", "error": "...", "stats": {...}}
render_markdown() turns it into trace.md, the readable version that proposers usually look at.
"""

import json
import time
from pathlib import Path


class Trace:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("")
        self._last_input: list[dict] = []

    def _write(self, event: dict):
        with self.path.open("a") as f:
            f.write(json.dumps({"t": round(time.time(), 2), **event}) + "\n")

    def log_query(self, messages: list[dict], response: str, **info):
        messages = [{"role": m["role"], "content": m["content"]} for m in messages]
        keep = 0
        while keep < min(len(messages), len(self._last_input)) and messages[keep] == self._last_input[keep]:
            keep += 1
        self._write({"type": "query", "keep": keep, "prev_len": len(self._last_input), "new": messages[keep:],
                     "response": response, **info})
        self._last_input = messages + [{"role": "assistant", "content": response}]

    def log_exec(self, command: str, output: dict, seconds: float):
        self._write({"type": "exec", "command": command, **output, "seconds": round(seconds, 2)})

    def log_end(self, **info):
        self._write({"type": "end", **info})


def render_markdown(path: Path, max_chars: int = 3000) -> str:
    """trace.jsonl -> Markdown: the conversation as the model saw it, with commands and long outputs shortened."""
    def clip(s):
        return s if len(s) <= max_chars else s[: max_chars // 2] + f"\n... [{len(s) - max_chars} chars cut] ...\n" + s[-max_chars // 2:]

    lines, n = [], 0
    for line in Path(path).read_text().splitlines():
        e = json.loads(line)
        if e["type"] == "query":
            n += 1
            if e["keep"] < e["prev_len"]:
                lines.append(f"\n> **The harness rewrote the history**: kept the first {e['keep']} messages\n")
            for m in e["new"]:
                lines.append(f"\n### [{m['role']}]\n\n{clip(m['content'])}\n")
            lines.append(f"\n### [assistant] step {n} ({e.get('seconds', 0):.0f}s, ${e.get('cost', 0):.4f})\n\n"
                         f"{clip(e['response'])}\n")
        elif e["type"] == "exec":
            flag = " TIMED OUT" if e.get("timed_out") else ""
            lines.append(f"\n> exec ({e['seconds']:.1f}s, returncode {e['returncode']}{flag}): "
                         f"`{e['command'][:200].replace(chr(10), ' ')}`\n")
        elif e["type"] == "end":
            lines.append(f"\n---\n**END** {json.dumps({k: v for k, v in e.items() if k not in ('t', 'type')})}\n")
    return "".join(lines)
