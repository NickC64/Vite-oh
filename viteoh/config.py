from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    service_role: str = Field(default="receiver", pattern="^(receiver|worker|web)$")
    google_cloud_project: str = ""
    google_cloud_location: str = "northamerica-northeast1"
    firestore_database: str = "(default)"
    interaction_queue: str = "viteoh-interactions"
    deadline_queue: str = "viteoh-deadlines"
    workspace_queue: str = "viteoh-workspace"
    worker_url: str = "http://localhost:8081"
    workspace_url: str = "http://localhost:8082"
    task_invoker_service_account: str = ""

    discord_application_id: str = ""
    discord_public_key: str = ""
    discord_bot_token: str = ""
    discord_owner_user_id: str = ""
    discord_api_base_url: str = "https://discord.com/api/v10"

    signature_max_age_seconds: int = Field(default=300, ge=30, le=900)
    workspace_signing_secret: str = ""
    proposal_ownership_secret: str = ""
    workspace_launch_ttl_seconds: int = Field(default=300, ge=60, le=900)
    workspace_session_ttl_seconds: int = Field(
        default=7 * 24 * 60 * 60, ge=3600, le=30 * 24 * 60 * 60
    )
    workspace_job_ttl_seconds: int = Field(
        default=24 * 60 * 60, ge=3600, le=7 * 24 * 60 * 60
    )
    workspace_auth_cache_seconds: int = Field(default=300, ge=0, le=300)
    secure_cookies: bool = True
    asset_version: str = Field(default="dev", validation_alias="K_REVISION")


@lru_cache
def get_settings() -> Settings:
    return Settings()
