"""
Application configuration loaded from environment variables.
Provides safe defaults for local development only.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DATABASE: str = "hrone_attendance"


# Single shared settings instance used throughout the application.
settings = Settings()
