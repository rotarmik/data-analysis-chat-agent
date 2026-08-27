"""Runtime context of the chat session, shared with tools."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


@dataclass
class SessionContext:
    username: str = "manager"
    session_id: str = field(default_factory=lambda: uuid4().hex[:12])
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    )


session = SessionContext()
