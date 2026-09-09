"""
Конфигурация factcheck-agent.
Version: 1.0.0 (на базе minibu v5.5.0)
"""
from typing import Optional, Dict, List
from pathlib import Path
from datetime import datetime
from functools import lru_cache
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderSettings(BaseModel):
    base_url: str
    api_key: Optional[str] = None
    uri_format: str = "{model}"
    display_name: str = ""


class Settings(BaseSettings):
    # Секреты (все опциональны — активный провайдер через default_provider)
    yandex_api_key: Optional[str] = None
    yandex_folder_id: Optional[str] = None
    router_ai_key: Optional[str] = None

    default_provider: str = "router"

    # Модели
    yandex_model_router: str = "aliceai-llm-flash/latest"
    yandex_model_agent: str = "qwen3.6-35b-a3b/latest"
    router_model: str = "z-ai/glm-5.3-flash"

    # Модели-чекеры (2–4 штуки, через запятую). Минимум одна должна уметь vision.
    checker_models: str = "z-ai/glm-5.3-flash,google/gemini-3.5-flash-lite,inception/mercury-2.5"
    arbiter_model: str = ""          # пусто => model_agent активного провайдера

    # Каталоги
    input_dir: Path = Path("input")
    output_dir: Path = Path("output")
    num_checkers_min: int = 2
    num_checkers_max: int = 4
    checker_max_iterations: int = 8   # бюджет tool-вызовов чекера (+1 финальный без тулов)

    # 🚧 MCP ЗАРЕЗЕРВИРОВАН (скрапинг источников подключим позже)
    mcp_enabled: bool = False
    mcp_url: str = ""

    # Системное
    log_file: str = "logs.txt"
    system_version: str = "factcheck_v1.0"
    yandex_base_url: str = "https://ai.api.cloud.yandex.net/v1"
    router_base_url: str = "https://routerai.ru/api/v1"
    skills_dir: Path = Path(".agents/skills")
    prompts_dir: Path = Path(".agents/prompts")

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore",
    )

    @property
    def providers(self) -> Dict[str, ProviderSettings]:
        return {
            "yandex": ProviderSettings(
                base_url=self.yandex_base_url,
                api_key=self.yandex_api_key,
                uri_format=f"gpt://{self.yandex_folder_id}/{{model}}",
                display_name="Yandex AI Studio",
            ),
            "router": ProviderSettings(
                base_url=self.router_base_url,
                api_key=self.router_ai_key,
                uri_format="{model}",
                display_name="Router AI",
            ),
        }

    @property
    def provider(self) -> ProviderSettings:
        return self.providers[self.default_provider]

    @property
    def model_router(self) -> str:
        return self.yandex_model_router if self.default_provider == "yandex" else self.router_model

    @property
    def model_agent(self) -> str:
        return self.yandex_model_agent if self.default_provider == "yandex" else self.router_model

    @property
    def checker_model_list(self) -> List[str]:
        models = [m.strip() for m in self.checker_models.split(",") if m.strip()]
        models = models[: self.num_checkers_max]
        if len(models) < self.num_checkers_min:
            raise ValueError(
                f"Нужно {self.num_checkers_min}–{self.num_checkers_max} модели-чекера "
                f"(CHECKER_MODELS в .env), получено {len(models)}"
            )
        return models

    @property
    def arbiter(self) -> str:
        return self.arbiter_model or self.model_agent

    def build_model_uri(self, model: str) -> str:
        if model.startswith(("gpt://", "ds://")):
            return model
        return self.provider.uri_format.format(model=model)

    def get_current_date_context(self) -> str:
        now = datetime.now()
        return f"Сегодня {now.strftime('%d.%m.%Y')}, {now.strftime('%A')}."


@lru_cache()
def get_settings() -> Settings:
    return Settings()

settings = get_settings()