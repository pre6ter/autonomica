"""Конфигурация сервиса. Значения читаются из переменных окружения / .env."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LM Studio
    lmstudio_base_url: str = "http://localhost:1234/v1"
    lmstudio_api_key: str = "lm-studio"
    model_name: str = "qwen3-30b-a3b"

    # Восприятие
    vision_enabled: bool = False

    # Агентный цикл
    max_steps: int = 40
    step_delay: float = 0.8
    temperature: float = 0.1
    max_tokens: int = 1024
    screenshot_max_side: int = 1280
    monitor_index: int = 1

    # Ввод
    input_backend: str = "pyautogui"  # pyautogui | xdotool | ydotool

    # HTTP
    host: str = "127.0.0.1"
    port: int = 8077

    # Безопасность
    service_api_key: str = ""

    # Логи
    log_level: str = "INFO"
    log_dir: str = "logs"
    save_step_screenshots: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
