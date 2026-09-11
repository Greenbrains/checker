"""
Конфигурация factcheck-agent.
Version: 2.1.0
Изменения 2.1.0:
    - checker_model_list: валидация через @model_validator (post) вместо property —
      ошибка бросается при создании объекта до @lru_cache, кэш не ломается;
    - arbiter, model_router, model_agent переведены на @model_validator для
      единственного места резолюции зависимых значений;
    - убрана избыточная переменная settings на уровне модуля (используй get_settings()).
"""
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderSettings(BaseModel):
    base_url: str
    api_key: Optional[str] = None
    uri_format: str = "{model}"
    display_name: str = ""


class Settings(BaseSettings):
    # Секреты (активный провайдер выбирается через default_provider)
    yandex_api_key: Optional[str] = None
    yandex_folder_id: Optional[str] = None
    router_ai_key: Optional[str] = None

    default_provider: str = "router"

    # Базовые модели провайдеров
    yandex_model_router: str = "aliceai-llm-flash/latest"
    yandex_model_agent: str = "qwen3.6-35b-a3b/latest"
    router_model: str = "z-ai/glm-5.3-flash"

    # Модели-чекеры (2–4 штуки, через запятую)
    checker_models: str = "z-ai/glm-5.3-flash,google/gemini-3.5-flash-lite"
    arbiter_model: str = ""  # пусто => model_agent активного провайдера

    # Каталоги
    input_dir: Path = Path("input")
    output_dir: Path = Path("output")
    num_checkers_min: int = 2
    num_checkers_max: int = 3  #  чекера
    checker_max_iterations: int = 9  # бюджет tool-вызовов чекера (+1 финальный без тулов)

    # 🚧 MCP ЗАРЕЗЕРВИРОВАН (скрапинг источников подключим позже)
    mcp_enabled: bool = False
    mcp_url: str = ""

    # Пайплайн: "v1" = параллельные чекеры; "v2" = claims-based
    pipeline_version: str = "v3.1"

    # Системное
    log_file: str = "logs.txt"
    system_version: str = "factcheck_v1.0"
    yandex_base_url: str = "https://ai.api.cloud.yandex.net/v1"
    router_base_url: str = "https://routerai.ru/api/v1"
    skills_dir: Path = Path(".agents/skills")
    prompts_dir: Path = Path(".agents/prompts")

    # Вычисляемые поля (заполняются в model_validator ниже)
    _checker_model_list: List[str] = []
    _arbiter: str = ""
    _model_router: str = ""
    _model_agent: str = ""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore",
    )

    @model_validator(mode="after")
    def _resolve_derived(self) -> "Settings":
        # --- модели провайдера ---
        is_yandex = self.default_provider == "yandex"
        self._model_router = self.yandex_model_router if is_yandex else self.router_model
        self._model_agent = self.yandex_model_agent if is_yandex else self.router_model

        # --- список чекеров (валидация здесь не ломает lru_cache) ---
        models = [m.strip() for m in self.checker_models.split(",") if m.strip()]
        models = models[: self.num_checkers_max]
        if len(models) < self.num_checkers_min:
            raise ValueError(
                f"Нужно {self.num_checkers_min}–{self.num_checkers_max} модели-чекера "
                f"(CHECKER_MODELS в .env), получено {len(models)}"
            )
        self._checker_model_list = models

        # --- арбитр ---
        self._arbiter = self.arbiter_model or self._model_agent

        return self

    # ---------- провайдеры ----------

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

    # ---------- вычисляемые поля ----------

    @property
    def model_router(self) -> str:
        return self._model_router

    @property
    def model_agent(self) -> str:
        return self._model_agent

    @property
    def checker_model_list(self) -> List[str]:
        return self._checker_model_list

    @property
    def arbiter(self) -> str:
        return self._arbiter

    # ---------- утилиты ----------

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
