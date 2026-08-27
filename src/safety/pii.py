"""PII protection: block sensitive columns at SQL level, mask leaks at output level.

Defense in depth:
1. SQL validation (primary): queries referencing PII columns are rejected
   before execution, so raw PII never leaves BigQuery.
2. Output masking (safety net): the final answer is scanned for PII-looking
   patterns (emails, coordinates) and masked before display.
"""
import re

BLOCKED_COLUMNS = {
    "email",
    "street_address",
    "postal_code",
    "latitude",
    "longitude",
    "user_geom",
}

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_COORD_PAIR_RE = re.compile(r"-?\d{1,3}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}")


def find_blocked_columns(sql: str) -> list[str]:
    """Return PII columns referenced in the query, empty list if clean."""
    found = []
    for column in sorted(BLOCKED_COLUMNS):
        if re.search(rf"\b{column}\b", sql, flags=re.IGNORECASE):
            found.append(column)
    return found


def mask_pii(text: str) -> tuple[str, int]:
    """Mask PII-looking patterns in text. Returns (masked_text, replacements)."""
    masked, n_emails = _EMAIL_RE.subn("[email masked]", text)
    masked, n_coords = _COORD_PAIR_RE.subn("[location masked]", masked)
    return masked, n_emails + n_coords
