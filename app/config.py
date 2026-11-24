import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = ""
    mini_app_url: str = ""
    admin_telegram_ids: str = ""
    database_path: Path = Path(os.getenv("DATABASE_PATH", str(Path(os.getenv("DATA_DIR", "data")) / "bar.db")))
    storage_path: Path = Path(os.getenv("STORAGE_PATH", str(Path(os.getenv("DATA_DIR", "data")) / "storage")))
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    bot_proxy: str = ""
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def admin_ids(self) -> set[int]:
        return {int(value.strip()) for value in self.admin_telegram_ids.split(",") if value.strip().isdigit()}


settings = Settings()
data_dir = os.getenv("DATA_DIR")
if data_dir:
    # A relative DATABASE_PATH/STORAGE_PATH from .env should live under the
    # host's persistent DATA_DIR in containers (Bothost sets DATA_DIR=/app/data).
    if not settings.database_path.is_absolute() and settings.database_path.parts[:1] == ("data",):
        settings.database_path = Path(data_dir) / settings.database_path.name
    if not settings.storage_path.is_absolute() and settings.storage_path.parts[:1] == ("data",):
        settings.storage_path = Path(data_dir) / settings.storage_path.name
settings.database_path.parent.mkdir(parents=True, exist_ok=True)
settings.storage_path.mkdir(parents=True, exist_ok=True)
