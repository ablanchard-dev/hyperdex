"""HyperDex settings — env-driven, fail loud si secret manquant en live."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    hl_account_address: str = os.getenv("HL_ACCOUNT_ADDRESS", "")
    hl_api_private_key: str = os.getenv("HL_API_PRIVATE_KEY", "")
    hl_network: str = os.getenv("HL_NETWORK", "mainnet")
    database_url: str = os.getenv(
        "DATABASE_URL", "sqlite:///./data/hyperdex.db")
    paper_only: bool = os.getenv("PAPER_ONLY", "true").lower() == "true"
    live_enabled: bool = os.getenv("LIVE_ENABLED", "false").lower() == "true"


def get_settings() -> Settings:
    return Settings()
