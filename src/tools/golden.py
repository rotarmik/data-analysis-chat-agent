"""Golden Knowledge base: past Question -> SQL -> Report trios by human analysts.

Retrieval uses lexical overlap scoring over local JSON files; production design
swaps this for a vector search over the golden bucket (see docs/ARCHITECTURE.md).

New knowledge enters through a curation gate: the /good CLI command captures a
successful exchange into golden/candidates/, which retrieval deliberately
ignores. A human reviews the candidate and moves it into golden/ to publish.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.tools import tool

from src.config import settings

_STOPWORDS = {
    "the", "a", "an", "of", "for", "in", "on", "and", "or", "to", "is", "are",
    "what", "which", "how", "why", "our", "we", "me", "my", "do", "did", "does",
    "show", "give", "get", "list", "with", "by", "per", "vs", "versus",
}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS}


def _load_trios() -> list[dict]:
    """Load published trios. Candidates in golden/candidates/ are not matched."""
    trios = []
    for path in sorted(settings.golden_dir.glob("*.json")):
        trios.append(json.loads(path.read_text(encoding="utf-8")))
    return trios


def save_candidate(
    question: str, sqls: list[str], report: str, username: str, session_id: str, note: str = ""
) -> Path:
    """Capture a successful exchange as a candidate trio awaiting human review."""
    candidates_dir = settings.golden_dir / "candidates"
    candidates_dir.mkdir(exist_ok=True)

    keywords = [w for w in re.findall(r"[a-z0-9]+", question.lower()) if w not in _STOPWORDS]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    candidate = {
        "id": f"candidate-{stamp}",
        "question": question,
        "tags": sorted(set(keywords)),
        "sql": "\n\n-- next query --\n\n".join(sqls),
        "report": report,
        "review": {
            "status": "pending_review",
            "proposed_by": username,
            "session_id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": note,
        },
    }
    path = candidates_dir / f"{stamp}-{'-'.join(keywords[:4]) or 'trio'}.json"
    path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def search_trios(question: str, top_k: int = 2) -> list[dict]:
    query_tokens = _tokens(question)
    scored = []
    for trio in _load_trios():
        trio_tokens = _tokens(trio["question"]) | _tokens(" ".join(trio.get("tags", [])))
        if not trio_tokens:
            continue
        overlap = len(query_tokens & trio_tokens)
        if overlap:
            scored.append((overlap / len(query_tokens | trio_tokens), trio))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [trio for _, trio in scored[:top_k]]


@tool
def search_golden_examples(question: str) -> str:
    """Search the Golden Knowledge base of past analyst work (Question -> SQL -> Report).

    ALWAYS call this before writing SQL for an analytical question: it shows how
    expert analysts interpreted similar questions, which tables and filters they
    used, and how they structured their findings.
    """
    matches = search_trios(question)
    if not matches:
        return "No similar past analyses found. Proceed with your own SQL based on the schema."
    blocks = []
    for trio in matches:
        blocks.append(
            f"### Past analysis: {trio['question']}\n"
            f"SQL used by the analyst:\n```sql\n{trio['sql'].strip()}\n```\n"
            f"Analyst's report/approach:\n{trio['report'].strip()}"
        )
    return "\n\n".join(blocks)
