from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = Field("development", alias="APP_ENV")
    app_secret: str = Field("development-only-change-me", alias="APP_SECRET")
    token_encryption_key: str = Field("", alias="TOKEN_ENCRYPTION_KEY")
    service_url: str = Field("http://localhost:5062", alias="SERVICE_URL")
    port: int = Field(5062, alias="PORT")
    database_url: str = Field("sqlite:///./data/fastsocial.db", alias="DATABASE_URL")
    db_pool_size: int = Field(3, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(2, alias="DB_MAX_OVERFLOW")
    db_pool_timeout: int = Field(10, alias="DB_POOL_TIMEOUT")
    db_pool_recycle: int = Field(1800, alias="DB_POOL_RECYCLE")
    db_application_name: str = Field("fastsocial", alias="DB_APPLICATION_NAME")
    auto_create_schema: bool = Field(True, alias="AUTO_CREATE_SCHEMA")
    scheduler_enabled: bool = Field(True, alias="SCHEDULER_ENABLED")
    default_timezone: str = Field("Europe/Tallinn", alias="DEFAULT_TIMEZONE")
    dev_user_email: str = Field("", alias="DEV_USER_EMAIL")
    dev_user_password: str = Field("", alias="DEV_USER_PASSWORD")

    google_client_id: str = Field("", alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field("", alias="GOOGLE_CLIENT_SECRET")
    google_redirect_uri: str = Field("", alias="GOOGLE_REDIRECT_URI")
    google_allowed_domains: str = Field("", alias="GOOGLE_ALLOWED_DOMAINS")
    google_allowed_emails: str = Field("", alias="GOOGLE_ALLOWED_EMAILS")

    media_storage: str = Field("local", alias="MEDIA_STORAGE")
    media_local_dir: Path = Field(Path("./data/media"), alias="MEDIA_LOCAL_DIR")
    r2_endpoint: str = Field("", alias="R2_ENDPOINT")
    r2_region: str = Field("auto", alias="R2_REGION")
    r2_access_key_id: str = Field("", alias="R2_ACCESS_KEY_ID")
    r2_secret_access_key: str = Field("", alias="R2_SECRET_ACCESS_KEY")
    r2_bucket: str = Field("fastsocial-media", alias="R2_BUCKET")
    r2_key_prefix: str = Field("fastsocial", alias="R2_KEY_PREFIX")

    xai_api_key: str = Field("", alias="XAI_API_KEY")
    xai_base_url: str = Field("https://api.x.ai/v1", alias="XAI_BASE_URL")
    xai_model: str = Field("grok-4.5", alias="XAI_MODEL")
    openai_api_key: str = Field("", alias="OPENAI_API_KEY")
    openai_base_url: str = Field("https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_model: str = Field("gpt-5.6-terra", alias="OPENAI_MODEL")
    model_provider: str = Field("xai", alias="MODEL_PROVIDER")
    model_name: str = Field("grok-4.5", alias="MODEL_NAME")
    image_model: str = Field("grok-imagine-image-quality", alias="IMAGE_MODEL")
    video_model: str = Field("grok-imagine-video-1.5", alias="VIDEO_MODEL")
    openai_image_model: str = Field("gpt-image-2", alias="OPENAI_IMAGE_MODEL")
    openai_video_model: str = Field("sora-2-pro", alias="OPENAI_VIDEO_MODEL")
    model_server_allowed_emails: str = Field(
        "kaljuvee@gmail.com", alias="MODEL_SERVER_ALLOWED_EMAILS"
    )
    model_request_timeout: int = Field(300, alias="MODEL_REQUEST_TIMEOUT")

    x_client_id: str = Field("", alias="X_CLIENT_ID")
    x_client_secret: str = Field("", alias="X_CLIENT_SECRET")
    linkedin_client_id: str = Field("", alias="LINKEDIN_CLIENT_ID")
    linkedin_client_secret: str = Field("", alias="LINKEDIN_CLIENT_SECRET")
    linkedin_api_version: str = Field("202606", alias="LINKEDIN_API_VERSION")
    bluesky_app_password_enabled: bool = Field(True, alias="BLUESKY_APP_PASSWORD_ENABLED")

    arcade_api_key: str = Field("", alias="ARCADE_API_KEY")
    arcade_mcp_url: str = Field("", alias="ARCADE_MCP_URL")
    composio_api_key: str = Field("", alias="COMPOSIO_API_KEY")
    composio_mcp_url: str = Field("", alias="COMPOSIO_MCP_URL")

    postmark_server_token: str = Field("", alias="POSTMARK_SERVER_TOKEN")
    report_from_email: str = Field("reports@fastsocial.org", alias="REPORT_FROM_EMAIL")

    @field_validator("service_url")
    @classmethod
    def trim_service_url(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @property
    def google_callback_url(self) -> str:
        return self.google_redirect_uri or f"{self.service_url}/auth/google/callback"

    def google_email_allowed(self, email: str) -> bool:
        allowed_emails = {
            item.strip().lower() for item in self.google_allowed_emails.split(",") if item.strip()
        }
        allowed_domains = {
            item.strip().lower().lstrip("@")
            for item in self.google_allowed_domains.split(",")
            if item.strip()
        }
        if not allowed_emails and not allowed_domains:
            return True
        normalized = email.strip().lower()
        domain = normalized.rsplit("@", 1)[-1]
        return normalized in allowed_emails or domain in allowed_domains

    def server_model_access_allowed(self, email: str) -> bool:
        allowed = {
            item.strip().lower()
            for item in self.model_server_allowed_emails.split(",")
            if item.strip()
        }
        return email.strip().lower() in allowed

    def validate_security(self) -> None:
        if self.production and self.app_secret == "development-only-change-me":
            raise RuntimeError("APP_SECRET must be configured in production")
        if self.production and not self.token_encryption_key:
            raise RuntimeError("TOKEN_ENCRYPTION_KEY must be configured in production")


@lru_cache(maxsize=1)
def settings() -> Settings:
    value = Settings()
    value.validate_security()
    return value
