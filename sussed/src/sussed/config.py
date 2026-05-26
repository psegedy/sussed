"""
Configuration management using pydantic-settings 🔧

Load settings from environment variables and .env files.
"""

from functools import lru_cache

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: PostgresDsn = Field(
        default="postgresql+asyncpg://sussed:sussed_dev_password@localhost:5432/sussed",
        description="PostgreSQL connection URL",
    )

    # Scraping
    scrape_rate_limit: float = Field(
        default=1.0,
        description="Maximum requests per second",
    )
    user_agent: str = Field(
        default="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        description="User-Agent header for HTTP requests",
    )

    # AI (optional, for agno)
    openai_api_key: str | None = Field(default=None, description="OpenAI API key")
    anthropic_api_key: str | None = Field(default=None, description="Anthropic API key")

    # Logging
    log_level: str = Field(default="INFO", description="Logging level")

    # Notifications (Phase 3)
    discord_webhook_url: str | None = Field(default=None, description="Discord webhook URL")
    telegram_bot_token: str | None = Field(default=None, description="Telegram bot token")
    telegram_chat_id: str | None = Field(default=None, description="Telegram chat ID")


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
