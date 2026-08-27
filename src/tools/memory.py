"""User preference memory: the agent records durable presentation/analysis
preferences and they are injected into the system prompt of future sessions."""
from langchain_core.tools import tool

from src import storage
from src.context import session


@tool
def remember_preference(preference: str) -> str:
    """Remember a durable preference of the current user for future sessions.

    Call this whenever the user expresses how they like their analysis served,
    e.g. 'prefers tables over prose', 'wants brief answers', 'always wants
    action items included'. State the preference as a short third-person fact.
    """
    storage.add_preference(session.username, preference)
    return f"Remembered: {preference}"


@tool
def forget_preference(preference: str) -> str:
    """Remove a stored preference of the current user that no longer applies.

    Call this when the user retracts or changes a preference. Quote the
    preference exactly as it appears in the known-preferences list. To replace
    a preference, call forget_preference for the outdated one, then
    remember_preference for the new one.
    """
    if storage.remove_preference(session.username, preference):
        return f"Forgot: {preference}"
    stored = storage.list_preferences(session.username)
    matches = [p for p in stored if preference.lower() in p.lower()]
    if len(matches) == 1:
        storage.remove_preference(session.username, matches[0])
        return f"Forgot: {matches[0]}"
    if matches:
        joined = "; ".join(matches)
        return f"Ambiguous — several stored preferences match: {joined}. Call again with the exact text."
    joined = "; ".join(stored) if stored else "none"
    return f"No stored preference matches '{preference}'. Currently stored: {joined}."


def load_preferences(username: str) -> list[str]:
    return storage.list_preferences(username)
