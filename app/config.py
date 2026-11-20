from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = ""
    mini_app_url: str = ""
    admin_telegram_ids: str = ""
    database_path: Path = Path("data/bar.db")
    storage_path: Path = Path("data/storage")
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    bot_proxy: str = ""
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def admin_ids(self) -> set[int]:
        return {int(value.strip()) for value in self.admin_telegram_ids.split(",") if value.strip().isdigit()}


settings = Settings()
settings.database_path.parent.mkdir(parents=True, exist_ok=True)
settings.storage_path.mkdir(parents=True, exist_ok=True)
