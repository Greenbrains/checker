"""
PromptLoader — сборка системного промпта: base(YAML) + дата + активный навык.
Version: 1.0.0
"""
import yaml
from config.settings import get_settings

settings = get_settings()

class PromptLoader:
    def __init__(self, prompts_dir=None):
        path = (prompts_dir or settings.prompts_dir) / "system.yaml"
        self.data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def render_system_prompt(self, skill_context: str) -> str:
        return (
            self.data.get("system_prompt", "Ты — фактчекер.").strip()
            + f"\n\n## Текущая дата\n{settings.get_current_date_context()}"
            + f"\n\n## АКТИВНЫЙ НАВЫК (обязателен к исполнению)\n{skill_context}"
        )