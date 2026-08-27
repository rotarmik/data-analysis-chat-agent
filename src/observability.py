"""Structured tracing: every LLM call and tool call is appended to a JSONL
trace file per session, with latency, token usage and errors. The CLI exposes
/trace (step-by-step view) and /metrics (session counters).

When Langfuse credentials are present in .env, the same events are also
shipped to Langfuse (nested traces, token costs, sessions/users) — the
production observability path, optional in the prototype.
"""
import json
import os
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from src.config import settings
from src.context import session


def build_langfuse() -> tuple:
    """Return (callback_handler, client) if Langfuse is configured, else (None, None).

    Never raises: missing keys or an unreachable Langfuse must not take the
    chat down — local JSONL tracing keeps working either way.
    """
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        return None, None
    try:
        from langfuse import get_client
        from langfuse.langchain import CallbackHandler

        client = get_client()
        return CallbackHandler(), client
    except Exception as exc:
        print(f"Langfuse disabled ({exc})")
        return None, None


class TraceLogger(BaseCallbackHandler):
    def __init__(self) -> None:
        self.path = settings.data_dir / "traces" / f"{session.started_at}-{session.session_id}.jsonl"
        self._started: dict[UUID, float] = {}
        self.metrics = {
            "llm_calls": 0,
            "tool_calls": 0,
            "llm_errors": 0,
            "tool_errors": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "llm_seconds": 0.0,
        }

    def _write(self, event: str, **payload: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "session": session.session_id,
            "user": session.username,
            "event": event,
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs) -> None:
        self._started[run_id] = time.monotonic()
        last = messages[0][-1] if messages and messages[0] else None
        self._write("llm_start", last_message=getattr(last, "content", "")[:500])

    def on_llm_end(self, response, *, run_id, **kwargs) -> None:
        elapsed = time.monotonic() - self._started.pop(run_id, time.monotonic())
        usage = {}
        try:
            usage = response.generations[0][0].message.usage_metadata or {}
        except (AttributeError, IndexError):
            pass
        self.metrics["llm_calls"] += 1
        self.metrics["llm_seconds"] += elapsed
        self.metrics["input_tokens"] += usage.get("input_tokens", 0)
        self.metrics["output_tokens"] += usage.get("output_tokens", 0)
        self._write("llm_end", seconds=round(elapsed, 2), usage=usage)

    def on_llm_error(self, error, *, run_id, **kwargs) -> None:
        self.metrics["llm_errors"] += 1
        self._write("llm_error", error=str(error))

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs) -> None:
        self._started[run_id] = time.monotonic()
        self._write("tool_start", tool=serialized.get("name"), input=input_str[:1000])

    def on_tool_end(self, output, *, run_id, **kwargs) -> None:
        elapsed = time.monotonic() - self._started.pop(run_id, time.monotonic())
        text = str(getattr(output, "content", output))
        self.metrics["tool_calls"] += 1
        if text.startswith(("SQL_ERROR", "REJECTED", "SCHEMA_ERROR", "EMPTY_RESULT")):
            self.metrics["tool_errors"] += 1
        self._write("tool_end", seconds=round(elapsed, 2), output=text[:1000])

    def on_tool_error(self, error, *, run_id, **kwargs) -> None:
        self.metrics["tool_errors"] += 1
        self._write("tool_error", error=str(error))

    def read_trace(self, last_n: int = 40) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").strip().splitlines()
        return [json.loads(line) for line in lines[-last_n:]]
