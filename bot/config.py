from __future__ import annotations

import logging
from pathlib import Path
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
GENERATED_DIR = BASE_DIR / "generated"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "sqlite+aiosqlite:///bot.db"

    gigachat_client_id: str = ""
    gigachat_client_secret: str = ""
    gigachat_scope: str = "GIGACHAT_API_PERS"

    log_level: str = "INFO"

    nds_rate: float = Field(default=5.0, description="НДС rate in %")
    protocol_fee_rate: float = Field(
        default=3.0, description="Оформление протоколов rate in %"
    )

    anti_flood_rate: float = Field(
        default=1.5, description="Min seconds between messages"
    )
    spam_warn_threshold: int = 5
    spam_block_threshold: int = 10
    spam_block_duration: int = 300
    max_message_length: int = 2000
    fsm_ttl: int = 86400

    llm_confidence_threshold: float = 0.75

    executor_name: str = 'ООО "АНАЛИТИК.ЛАБ"'
    executor_inn: str = "7806341520"
    executor_kpp: str = "781601001"
    executor_address: str = (
        "192102, Город Санкт-Петербург, вн.тер. г. Муниципальный Округ "
        "Волковское, ул Дубровская, дом 13, литера А, помещение 2-Н, комната 27"
    )

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        v = v.upper()
        if v not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError(f"Invalid log_level: {v}")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
