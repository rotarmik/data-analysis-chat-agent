"""Saved Reports library. Deletion is destructive and gated by a human
confirmation interrupt configured in the agent (see src/agent.py)."""
from langchain_core.tools import tool

from src import storage
from src.context import session


@tool
def save_report(title: str, content: str) -> str:
    """Save a finished report to the user's Saved Reports library.

    Call this when the user asks to save/store a report. Pass the full report
    text in markdown as content.
    """
    report_id = storage.save_report(session.username, title, content, session.session_id)
    return f"Report saved with id {report_id}: '{title}'."


@tool
def list_saved_reports() -> str:
    """List the user's saved reports (id, title, creation date, session)."""
    reports = storage.list_reports(session.username)
    if not reports:
        return "No saved reports."
    lines = ["Saved reports:"]
    for r in reports:
        current = " (current session)" if r["session_id"] == session.session_id else ""
        lines.append(f"- id={r['id']} | {r['title']} | {r['created_at']}{current}")
    return "\n".join(lines)


@tool
def read_saved_report(report_id: int) -> str:
    """Read the full content of one saved report by id."""
    report = storage.get_report(session.username, report_id)
    if not report:
        return f"No report with id {report_id} in your library."
    return f"# {report['title']}\n\n{report['content']}"


@tool
def delete_saved_reports(report_ids: list[int], reason: str) -> str:
    """Permanently delete saved reports by id. DESTRUCTIVE — requires user confirmation.

    Before calling: use list_saved_reports (and read_saved_report if needed) to
    determine exactly which reports match the user's request. Pass the matching
    ids and a short reason describing what is being deleted and why.
    """
    deleted = storage.delete_reports(session.username, report_ids)
    return f"Deleted {deleted} report(s): {report_ids}."
