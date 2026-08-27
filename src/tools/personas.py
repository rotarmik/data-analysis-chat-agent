"""Persona management by conversation: executives change the report tone by
asking for it in chat. Updates are gated by a confirmation interrupt because a
persona change affects every user of that persona."""
from langchain_core.tools import tool

from src.personas import available_personas, load_persona, save_persona


@tool
def read_persona(name: str) -> str:
    """Read the current description and style of a persona.

    Always call this before updating an existing persona, so your update
    preserves what should be kept.
    """
    if name not in available_personas():
        return f"No persona '{name}'. Available: {', '.join(available_personas())}."
    persona = load_persona(name)
    return f"Persona '{name}':\ndescription: {persona['description']}\nstyle:\n{persona['style']}"


@tool
def update_persona(name: str, description: str, style: str) -> str:
    """Create or update a persona — the tone-of-voice instructions for reports.

    Use when the user asks to change how reports sound (more formal, shorter,
    add a risks section, new persona for board meetings, etc.). Pass the FULL
    new style as a markdown bullet list — it replaces the old one entirely.
    Changes apply from the next message; the system asks the user to confirm
    before saving.
    """
    is_new = name not in available_personas()
    try:
        save_persona(name, description, style)
    except ValueError as exc:
        return f"REJECTED: {exc}"
    action = "created" if is_new else "updated"
    return f"Persona '{name}' {action}. It takes effect from the next message; switch with /persona {name}."
