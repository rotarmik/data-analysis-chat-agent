"""Persona storage: tone-of-voice configs editable by non-developers.

Personas are plain YAML files hot-reloaded on every turn, so edits (manual or
via the update_persona tool) apply from the next message without redeployment.
"""
import re
from pathlib import Path

import yaml

from src.config import settings

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")


def persona_path(name: str) -> Path:
    return settings.personas_dir / f"{name}.yaml"


def available_personas() -> list[str]:
    return sorted(p.stem for p in settings.personas_dir.glob("*.yaml"))


def load_persona(name: str) -> dict:
    return yaml.safe_load(persona_path(name).read_text(encoding="utf-8"))


def save_persona(name: str, description: str, style: str) -> Path:
    if not _NAME_RE.match(name):
        raise ValueError(f"Invalid persona name '{name}': use lowercase letters, digits, - or _.")
    if not style.strip():
        raise ValueError("Persona style must not be empty.")
    path = persona_path(name)
    content = yaml.safe_dump(
        {"name": name, "description": description.strip(), "style": style.strip() + "\n"},
        sort_keys=False,
        allow_unicode=True,
        default_style=None,
    )
    path.write_text(content, encoding="utf-8")
    return path
