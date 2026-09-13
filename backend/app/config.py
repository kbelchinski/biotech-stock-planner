"""Application configuration, loaded from environment variables / the repo-root .env file.

Credentials are held as SecretStr so they never appear in reprs, logs, or API responses.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]


class AppMode(StrEnum):
    DEMO = "demo"
    LIVE = "live"


class BpiqAccessTier(StrEnum):
    APEX_PAID = "apex_paid"
    APEX_TRIAL = "apex_trial"


class AlpacaAccountType(StrEnum):
    PAPER = "paper"
    LIVE = "live"


# Apex trial: "/api/v1/info/catalysts/ — Next 30 days only" (BPIQ access matrix).
BPIQ_TRIAL_HORIZON_DAYS = 30
# "Rate limits by plan": Apex Trial 10 req/min, Apex Paid 15 req/min.
BPIQ_RATE_LIMITS_PER_MIN = {BpiqAccessTier.APEX_TRIAL: 10, BpiqAccessTier.APEX_PAID: 15}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_mode: AppMode = AppMode.DEMO

    bpiq_api_key: SecretStr | None = None
    bpiq_base_url: str = "https://api.bpiq.com/api/v1/info"
    bpiq_access_tier: BpiqAccessTier = BpiqAccessTier.APEX_PAID
    # Maximum `limit` is not documented; `next` links are followed regardless of server-side caps.
    bpiq_page_size: int = 50

    alpaca_api_key_id: SecretStr | None = None
    alpaca_api_secret_key: SecretStr | None = None
    alpaca_account_type: AlpacaAccountType = AlpacaAccountType.PAPER
    alpaca_data_base_url: str = "https://data.alpaca.markets"
    # Basic plan: 200 req/min; Algo Trader Plus: 10,000 req/min. Default to the lower bound.
    alpaca_rate_limit_per_min: int = 180

    http_timeout_seconds: float = 20.0
    http_max_retries: int = 3

    database_path: Path = BACKEND_ROOT / "data" / "scans.sqlite3"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def alpaca_trading_base_url(self) -> str:
        if self.alpaca_account_type is AlpacaAccountType.LIVE:
            return "https://api.alpaca.markets"
        return "https://paper-api.alpaca.markets"

    @property
    def bpiq_configured(self) -> bool:
        return bool(self.bpiq_api_key and self.bpiq_api_key.get_secret_value().strip())

    @property
    def alpaca_configured(self) -> bool:
        return bool(
            self.alpaca_api_key_id
            and self.alpaca_api_key_id.get_secret_value().strip()
            and self.alpaca_api_secret_key
            and self.alpaca_api_secret_key.get_secret_value().strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
