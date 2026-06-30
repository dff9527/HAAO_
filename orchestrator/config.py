from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    claude_api_key: str = Field(default="", alias="CLAUDE_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    lmstudio_base_url: str = Field(
        default="http://localhost:1234/v1",
        alias="LMSTUDIO_BASE_URL",
    )
    local_max_output_tokens: int = Field(
        default=4096,
        ge=1,
        alias="LOCAL_MAX_OUTPUT_TOKENS",
    )
    local_patch_mode_threshold_tokens: int = Field(
        default=2048,
        ge=1,
        alias="LOCAL_PATCH_MODE_THRESHOLD_TOKENS",
    )
    database_url: str = Field(default="sqlite:///./haao.sqlite3", alias="DATABASE_URL")
    claude_model: str = Field(default="claude-sonnet-4-6", alias="CLAUDE_MODEL")
    haao_api_token: str = Field(default="", alias="HAAO_API_TOKEN")
    haao_sandbox_mode: str = Field(default="", alias="HAAO_SANDBOX_MODE")
    github_app_id: str = Field(default="", alias="GITHUB_APP_ID")
    github_app_private_key: str = Field(default="", alias="GITHUB_APP_PRIVATE_KEY")
    github_api_base_url: str = Field(
        default="https://api.github.com",
        alias="GITHUB_API_BASE_URL",
    )
    gitlab_api_base_url: str = Field(
        default="https://gitlab.com/api/v4",
        alias="GITLAB_API_BASE_URL",
    )
    gitlab_app_bootstrap_token: str = Field(
        default="",
        alias="GITLAB_APP_BOOTSTRAP_TOKEN",
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
