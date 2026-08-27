import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env", override=True)


@dataclass(frozen=True)
class Settings:
    gcp_project_id: str = os.getenv("GCP_PROJECT_ID", "")
    dataset_id: str = os.getenv("BQ_DATASET_ID", "bigquery-public-data.thelook_ecommerce")

    ollama_host: str = os.getenv("OLLAMA_HOST", "https://ollama.com")
    ollama_api_key: str = os.getenv("OLLAMA_API_KEY", "")
    model: str = os.getenv("OLLAMA_MODEL", "kimi-k3:cloud")
    fallback_model: str = os.getenv("OLLAMA_FALLBACK_MODEL", "")

    data_dir: Path = field(default=PROJECT_ROOT / "data")
    personas_dir: Path = field(default=PROJECT_ROOT / "personas")
    golden_dir: Path = field(default=PROJECT_ROOT / "golden")

    default_persona: str = os.getenv("DEFAULT_PERSONA", "executive")

    max_bytes_billed: int = int(os.getenv("BQ_MAX_BYTES_BILLED", str(2 * 1024**3)))
    max_result_rows: int = int(os.getenv("BQ_MAX_RESULT_ROWS", "50"))
    max_sql_attempts: int = int(os.getenv("MAX_SQL_ATTEMPTS", "3"))
    recursion_limit: int = int(os.getenv("AGENT_RECURSION_LIMIT", "50"))


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
(settings.data_dir / "traces").mkdir(parents=True, exist_ok=True)
