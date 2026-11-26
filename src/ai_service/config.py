from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    app_name: str = "ai-service"
    log_level: str = "INFO"
    llm_api_key: str = ""
    # llm_secret: str = ""


settings = Settings()
