"""Application configuration loaded from environment variables / .env file."""

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    builtwith_api_key: Optional[str] = None
    pagespeed_api_key: Optional[str] = None
    google_places_api_key: Optional[str] = None
    gateway_base_url: str = "https://api.openai.com/v1"
    gateway_api_key: Optional[str] = None
    vision_model: str = "gpt-4o"
    screenshot_dir: str = "/tmp/lyvica_screenshots"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
