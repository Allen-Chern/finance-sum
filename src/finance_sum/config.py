"""Application configuration — loads from .env and environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _project_root() -> Path:
    """Walk up from this file to find the project root (contains pyproject.toml)."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    return Path.cwd()


PROJECT_ROOT = _project_root()


@dataclass(frozen=True)
class Config:
    """Immutable application configuration."""

    # Storage (Options: "notion", "json")
    storage_backend: str = "notion"

    # Notion
    notion_api_key: str = ""
    notion_balance_db_id: str = ""
    notion_credit_card_db_id: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Playwright
    headless: bool = True

    # Paths
    key_path: Path = field(default_factory=lambda: PROJECT_ROOT / ".key")
    credentials_path: Path = field(
        default_factory=lambda: Path.home() / ".config" / "finance-sum" / "credentials.json"
    )
    local_data_path: Path = field(
        default_factory=lambda: Path.home() / ".config" / "finance-sum" / "data.json"
    )

    @classmethod
    def load(cls) -> Config:
        """Load config from .env file and environment variables."""
        load_dotenv(PROJECT_ROOT / ".env")

        return cls(
            storage_backend=os.getenv("STORAGE_BACKEND", "notion").lower(),
            notion_api_key=os.getenv("NOTION_API_KEY", ""),
            notion_balance_db_id=os.getenv("NOTION_BALANCE_DB_ID", ""),
            notion_credit_card_db_id=os.getenv("NOTION_CREDIT_CARD_DB_ID", ""),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            headless=os.getenv("HEADLESS", "true").lower() in ("true", "1", "yes"),
        )
