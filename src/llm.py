"""Chat model factory for Ollama Cloud."""
from langchain_ollama import ChatOllama

from src.config import settings


def build_chat_model(model_name: str | None = None) -> ChatOllama:
    headers = {}
    if settings.ollama_api_key:
        headers["Authorization"] = f"Bearer {settings.ollama_api_key}"
    return ChatOllama(
        model=model_name or settings.model,
        base_url=settings.ollama_host,
        client_kwargs={"headers": headers},
        temperature=0.2,
    )
