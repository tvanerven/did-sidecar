from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    dataverse_url: str = Field(alias="DATAVERSE_URL")
    dataverse_api_token: str = Field(alias="DATAVERSE_API_TOKEN")
    pid_base_url: str = Field(alias="PID_BASE_URL")
    database_url: str = Field(alias="DATABASE_URL")

    did_signing_key_encrypted: str = Field(alias="DID_SIGNING_KEY_ENCRYPTED")
    did_signing_key_passphrase: str = Field(alias="DID_SIGNING_KEY_PASSPHRASE")
    dataverse_workflow_token: str = Field(alias="DATAVERSE_WORKFLOW_TOKEN")

    admin_token: str | None = Field(default=None, alias="ADMIN_TOKEN")


@lru_cache
def get_settings() -> Settings:
    return Settings()
