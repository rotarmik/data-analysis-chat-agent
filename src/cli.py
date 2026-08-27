"""Interactive CLI chat for the data analysis agent."""
import json

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from src import storage
from src.agent import build_agent
from src.personas import available_personas, load_persona
from src.config import settings
from src.context import session
from src.observability import TraceLogger, build_langfuse
from src.safety.pii import mask_pii
from src.tools.golden import save_candidate

console = Console()

HELP = """\
Ask questions about sales, customers and products in natural language.

Commands:
  /user <name>      switch user (preferences and reports are per-user)
  /persona [name]   show or switch report persona (hot-reloaded from personas/)
  /prefs            show remembered preferences of the current user
  /reports          list your saved reports
  /good [note]      propose the last exchange as a Golden Bucket candidate
                    (saved to golden/candidates/ for human review)
  /trace [n]        show last n trace events of this session (default 15)
  /metrics          show session metrics (LLM calls, tokens, errors)
  /exit             quit

Tip: to change the report tone for everyone, just ask in chat — e.g.
"make the executive persona more formal, end every report with a Risks
section". Persona changes are shown as a diff and require confirmation.
"""


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


class ChatCLI:
    def __init__(self) -> None:
        self.tracer = TraceLogger()
        self.langfuse_handler, self.langfuse = build_langfuse()
        self.checkpointer = InMemorySaver()
        self.persona = settings.default_persona
        self.thread = 0
        self.last_turn: dict | None = None

    @property
    def config(self) -> dict:
        callbacks = [self.tracer]
        if self.langfuse_handler:
            callbacks.append(self.langfuse_handler)
        return {
            "configurable": {"thread_id": f"{session.username}-{self.thread}"},
            "recursion_limit": settings.recursion_limit,
            "callbacks": callbacks,
            "run_name": "chat-turn",
            "metadata": {
                "langfuse_user_id": session.username,
                "langfuse_session_id": session.session_id,
                "langfuse_tags": [f"persona:{self.persona}"],
            },
        }

    def run(self) -> None:
        console.print(
            Panel(
                f"[bold]Retail Data Analysis Agent[/bold]\n"
                f"model: {settings.model} | dataset: {settings.dataset_id}\n"
                f"user: {session.username} | persona: {self.persona} | "
                f"langfuse: {'on' if self.langfuse_handler else 'off'}\n"
                f"Type /help for commands.",
                border_style="cyan",
            )
        )
        while True:
            try:
                text = console.input("[bold green]you>[/bold green] ").lstrip("﻿").strip()
            except (KeyboardInterrupt, EOFError):
                break
            if not text:
                continue
            if text.startswith("/"):
                if not self._command(text):
                    break
                continue
            self._chat_turn(text)
        if self.langfuse:
            self.langfuse.flush()
        console.print("[dim]bye[/dim]")

    # ---------- chat ----------

    def _chat_turn(self, text: str) -> None:
        before = dict(self.tracer.metrics)
        payload = {"messages": [HumanMessage(text)]}
        agent = build_agent(session.username, self.persona, self.checkpointer)
        try:
            self._run_with_resilience(agent, payload)
        except Exception as exc:
            console.print(f"[red]The agent could not complete this request: {exc}[/red]")
            console.print("[dim]The conversation is intact — try rephrasing or retry later.[/dim]")
            return
        answer = self._print_answer(agent)
        if answer:
            self.last_turn = {
                "question": text,
                "sqls": self._executed_sql(agent, text),
                "answer": answer,
            }
        self._print_footer(before)

    def _run_with_resilience(self, agent, payload) -> None:
        """Try primary model, resume-retry once, then fall back to the backup model."""
        plans = [(agent, payload), (agent, None)]
        if settings.fallback_model and settings.fallback_model != settings.model:
            fallback = build_agent(
                session.username, self.persona, self.checkpointer, settings.fallback_model
            )
            plans.append((fallback, None))
        last_error = None
        for i, (runner, input_payload) in enumerate(plans):
            if i == 1:
                console.print("[yellow]Model call failed, retrying...[/yellow]")
            elif i == 2:
                console.print(f"[yellow]Switching to fallback model {settings.fallback_model}...[/yellow]")
            if input_payload is None and not runner.get_state(self.config).values:
                input_payload = payload
            try:
                self._stream_until_done(runner, input_payload)
                return
            except Exception as exc:
                last_error = exc
        raise last_error

    def _stream_until_done(self, agent, payload) -> None:
        while True:
            interrupts = self._stream(agent, payload)
            if not interrupts:
                return
            payload = Command(resume={"decisions": self._review(interrupts)})

    def _stream(self, agent, payload):
        interrupts = None
        for update in agent.stream(payload, config=self.config, stream_mode="updates"):
            if "__interrupt__" in update:
                interrupts = update["__interrupt__"]
                continue
            for value in update.values():
                if isinstance(value, dict):
                    for msg in value.get("messages", []):
                        self._print_activity(msg)
        return interrupts

    def _print_activity(self, msg) -> None:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for call in msg.tool_calls:
                args = json.dumps(call["args"], ensure_ascii=False)
                if len(args) > 160:
                    args = args[:160] + "…"
                console.print(f"[dim]  → {call['name']} {args}[/dim]")

    def _review(self, interrupts) -> list[dict]:
        """Confirmation flow for gated actions (report deletion, persona updates)."""
        decisions = []
        for interrupt in interrupts:
            for request in interrupt.value["action_requests"]:
                if request["name"] == "update_persona":
                    decisions.append(self._review_persona_update(request["args"]))
                else:
                    decisions.append(self._review_deletion(request["args"]))
        return decisions

    def _review_deletion(self, args: dict) -> dict:
        console.print(
            Panel(
                f"[bold red]Destructive action requires confirmation[/bold red]\n\n"
                f"Action: delete saved reports\n"
                f"Report ids: {args.get('report_ids')}\n"
                f"Reason: {args.get('reason', '—')}",
                border_style="red",
            )
        )
        if Confirm.ask("Approve deletion?", default=False):
            console.print("[dim]  approved[/dim]")
            return {"type": "approve"}
        console.print("[dim]  rejected[/dim]")
        return {"type": "reject", "message": "User declined the deletion. Keep the reports."}

    def _review_persona_update(self, args: dict) -> dict:
        name = args.get("name", "?")
        current = load_persona(name)["style"] if name in available_personas() else "(new persona)"
        console.print(
            Panel(
                f"[bold yellow]Persona update requires confirmation[/bold yellow]\n\n"
                f"Persona: [bold]{name}[/bold] — {args.get('description', '')}\n\n"
                f"[dim]Current style:[/dim]\n{current.strip()}\n\n"
                f"[bold]Proposed style:[/bold]\n{args.get('style', '').strip()}",
                border_style="yellow",
            )
        )
        if Confirm.ask("Apply this persona change?", default=False):
            console.print("[dim]  applied[/dim]")
            return {"type": "approve"}
        console.print("[dim]  rejected[/dim]")
        return {"type": "reject", "message": "User declined the persona change. Keep the current style."}

    def _print_answer(self, agent) -> str:
        state = agent.get_state(self.config)
        messages = state.values.get("messages", [])
        answer = next(
            (_text(m.content) for m in reversed(messages) if isinstance(m, AIMessage) and _text(m.content).strip()),
            "",
        )
        if not answer:
            console.print("[red]No answer produced.[/red]")
            return ""
        masked, n_masked = mask_pii(answer)
        console.print(Panel(Markdown(masked), border_style="blue"))
        if n_masked:
            console.print(f"[yellow]⚠ {n_masked} PII value(s) were masked in this answer.[/yellow]")
        return masked

    def _executed_sql(self, agent, question: str) -> list[str]:
        """Collect the SQL of run_sql calls made after the given user message."""
        messages = agent.get_state(self.config).values.get("messages", [])
        start = 0
        for i, message in enumerate(messages):
            if isinstance(message, HumanMessage) and _text(message.content) == question:
                start = i
        sqls = []
        for message in messages[start:]:
            if isinstance(message, AIMessage):
                for call in message.tool_calls or []:
                    if call["name"] == "run_sql" and call["args"].get("sql"):
                        sqls.append(call["args"]["sql"])
        return sqls

    def _print_footer(self, before: dict) -> None:
        m = self.tracer.metrics
        console.print(
            f"[dim]{m['llm_calls'] - before['llm_calls']} LLM calls · "
            f"{m['tool_calls'] - before['tool_calls']} tool calls · "
            f"{m['input_tokens'] - before['input_tokens']}→{m['output_tokens'] - before['output_tokens']} tokens · "
            f"{m['llm_seconds'] - before['llm_seconds']:.1f}s LLM time[/dim]"
        )

    # ---------- commands ----------

    def _command(self, text: str) -> bool:
        parts = text.split(maxsplit=1)
        cmd, arg = parts[0].lower(), parts[1].strip() if len(parts) > 1 else ""
        if cmd == "/exit":
            return False
        if cmd == "/help":
            console.print(HELP)
        elif cmd == "/user":
            if not arg:
                console.print(f"Current user: {session.username}")
            else:
                session.username = arg
                self.thread += 1
                console.print(f"Switched to user [bold]{arg}[/bold] (fresh conversation).")
        elif cmd == "/persona":
            if not arg:
                console.print(f"Current persona: {self.persona}. Available: {', '.join(available_personas())}")
            elif arg in available_personas():
                self.persona = arg
                console.print(f"Persona switched to [bold]{arg}[/bold].")
            else:
                console.print(f"[red]Unknown persona '{arg}'. Available: {', '.join(available_personas())}[/red]")
        elif cmd == "/prefs":
            prefs = storage.list_preferences(session.username)
            console.print("\n".join(f"- {p}" for p in prefs) if prefs else "No preferences remembered yet.")
        elif cmd == "/reports":
            self._show_reports()
        elif cmd == "/good":
            self._propose_candidate(arg)
        elif cmd == "/trace":
            self._show_trace(int(arg) if arg.isdigit() else 15)
        elif cmd == "/metrics":
            self._show_metrics()
        else:
            console.print(f"[red]Unknown command {cmd}[/red]. Type /help.")
        return True

    def _propose_candidate(self, note: str) -> None:
        if not self.last_turn:
            console.print("[red]Nothing to capture yet — ask an analytical question first.[/red]")
            return
        if not self.last_turn["sqls"]:
            console.print("[red]The last exchange ran no SQL — only query-backed exchanges can become trios.[/red]")
            return
        path = save_candidate(
            question=self.last_turn["question"],
            sqls=self.last_turn["sqls"],
            report=self.last_turn["answer"],
            username=session.username,
            session_id=session.session_id,
            note=note,
        )
        console.print(
            f"Candidate trio saved to [bold]{path.relative_to(settings.golden_dir.parent)}[/bold] "
            f"({len(self.last_turn['sqls'])} SQL quer{'y' if len(self.last_turn['sqls']) == 1 else 'ies'}).\n"
            "[dim]Retrieval ignores candidates. To publish: review the file and move it into golden/ — "
            "it takes effect on the next question, no restart needed.[/dim]"
        )

    def _show_reports(self) -> None:
        reports = storage.list_reports(session.username)
        if not reports:
            console.print("No saved reports.")
            return
        table = Table(title=f"Saved reports of {session.username}")
        for col in ("id", "title", "created_at", "session"):
            table.add_column(col)
        for r in reports:
            table.add_row(str(r["id"]), r["title"], r["created_at"], r["session_id"])
        console.print(table)

    def _show_trace(self, n: int) -> None:
        events = self.tracer.read_trace(n)
        if not events:
            console.print("Trace is empty.")
            return
        for e in events:
            line = f"[dim]{e['ts'].split('T')[1]}[/dim] [bold]{e['event']}[/bold]"
            for key in ("tool", "seconds", "error"):
                if key in e and e[key] is not None:
                    line += f" {key}={e[key]}"
            console.print(line)
            detail = e.get("input") or e.get("output")
            if detail:
                console.print(f"    [dim]{detail[:200]}[/dim]")

    def _show_metrics(self) -> None:
        table = Table(title=f"Session metrics (trace: {self.tracer.path.name})")
        table.add_column("metric")
        table.add_column("value", justify="right")
        for key, value in self.tracer.metrics.items():
            table.add_row(key, f"{value:.1f}" if isinstance(value, float) else str(value))
        console.print(table)


def main() -> None:
    ChatCLI().run()


if __name__ == "__main__":
    main()
