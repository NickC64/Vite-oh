from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    service_role: str = Field(default="receiver", pattern="^(receiver|worker)$")
    google_cloud_project: str = ""
    google_cloud_location: str = "northamerica-northeast1"
    firestore_database: str = "(default)"
    interaction_queue: str = "viteoh-interactions"
    deadline_queue: str = "viteoh-deadlines"
    worker_url: str = "http://localhost:8081"
    task_invoker_service_account: str = ""

    discord_application_id: str = ""
    discord_public_key: str = ""
    discord_bot_token: str = ""
    discord_owner_user_id: str = ""
    discord_api_base_url: str = "https://discord.com/api/v10"

    signature_max_age_seconds: int = Field(default=300, ge=30, le=900)


@lru_cache
def get_settings() -> Settings:
    return Settings()
