"""Read-only BigQuery access with SQL guards, cost caps and self-correction hints.

Tool errors are returned as strings (not raised) so the agent can read them
and correct its query instead of crashing the conversation.
"""
import re

from google.cloud import bigquery
from langchain_core.tools import tool

from src.config import settings
from src.safety.pii import BLOCKED_COLUMNS, find_blocked_columns

FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|CALL|EXECUTE|EXPORT)\b",
    flags=re.IGNORECASE,
)

_client: bigquery.Client | None = None


def _get_client() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(project=settings.gcp_project_id)
    return _client


def _validate(sql: str) -> str | None:
    """Return a rejection message, or None if the query is allowed."""
    stripped = sql.strip().rstrip(";")
    if ";" in stripped:
        return "REJECTED: multiple SQL statements are not allowed. Send a single SELECT query."
    if not re.match(r"^\s*(SELECT|WITH)\b", stripped, flags=re.IGNORECASE):
        return "REJECTED: only SELECT queries are allowed against this read-only database."
    if FORBIDDEN_KEYWORDS.search(stripped):
        keyword = FORBIDDEN_KEYWORDS.search(stripped).group(1).upper()
        return f"REJECTED: keyword '{keyword}' is not allowed. The database is read-only."
    blocked = find_blocked_columns(stripped)
    if blocked:
        return (
            f"REJECTED: query references PII columns which are forbidden: {', '.join(blocked)}. "
            "Rewrite the query without these columns. Use user id / first name for identification "
            "and city/state/country for geography."
        )
    return None


@tool
def run_sql(sql: str) -> str:
    """Execute a read-only SQL query against the BigQuery e-commerce dataset.

    Use fully qualified table names like `bigquery-public-data.thelook_ecommerce.orders`.
    Returns rows as a markdown table (capped), or an error message you should
    analyze and fix — do not resend a failed query unchanged.
    """
    rejection = _validate(sql)
    if rejection:
        return rejection

    job_config = bigquery.QueryJobConfig(
        maximum_bytes_billed=settings.max_bytes_billed,
        use_query_cache=True,
    )
    try:
        df = _get_client().query(sql, job_config=job_config).result().to_dataframe()
    except Exception as exc:
        return f"SQL_ERROR: {exc}\nAnalyze the error, fix the query and retry (max {settings.max_sql_attempts} attempts total)."

    # Second PII layer: SELECT * style queries pass text validation but can
    # still pull blocked columns — strip them from the result itself.
    leaked = [c for c in df.columns if c.lower() in BLOCKED_COLUMNS]
    pii_note = ""
    if leaked:
        df = df.drop(columns=leaked)
        pii_note = f"\n\nNote: PII columns were removed from the result: {', '.join(leaked)}."

    if df.empty:
        return (
            "EMPTY_RESULT: the query ran successfully but returned 0 rows. "
            "Check filter values (e.g. status/category/state spelling, date ranges) before retrying. "
            "If the emptiness is genuine, report that to the user honestly."
        )

    total = len(df)
    preview = df.head(settings.max_result_rows)
    table = preview.to_markdown(index=False)
    suffix = f"\n\n(showing {len(preview)} of {total} rows)" if total > len(preview) else f"\n\n({total} rows)"
    return table + suffix + pii_note


@tool
def get_table_schema(table_name: str) -> str:
    """Get the column list (name, type, description) for one dataset table.

    Valid tables: orders, order_items, products, users.
    """
    try:
        table = _get_client().get_table(f"{settings.dataset_id}.{table_name}")
    except Exception as exc:
        return f"SCHEMA_ERROR: {exc}"
    lines = [f"Schema of {settings.dataset_id}.{table_name}:"]
    for f in table.schema:
        desc = f" — {f.description}" if f.description else ""
        lines.append(f"- {f.name} ({f.field_type}){desc}")
    return "\n".join(lines)
