from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class PromptDefinition(BaseModel):
    version: str
    active: bool = True
    model: str
    fallback_model: str | None = None
    system: str
    user: str
    temperature: float = 0.0
    max_tokens: int = 1024
    experiment_group: str | None = None


class PromptRegistry:
    """Loads versioned YAML prompts from disk. Thread-safe after init."""

    _PROMPTS_DIR = Path(__file__).parent / "prompts"

    def __init__(self) -> None:
        self._prompts: dict[str, PromptDefinition] = {}
        self._load_all()

    def _load_all(self) -> None:
        for yaml_file in self._PROMPTS_DIR.glob("*.yaml"):
            raw = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
            versions: list[dict[str, Any]] = raw if isinstance(raw, list) else [raw]
            # The last active version wins
            for v in versions:
                definition = PromptDefinition(**v)
                key = yaml_file.stem
                versioned_key = f"{key}:{definition.version}"
                self._prompts[versioned_key] = definition
                if definition.active:
                    self._prompts[key] = definition

    def get(self, key: str) -> PromptDefinition:
        """Get active prompt by base key (e.g. 'entity_action_labeling')."""
        if key not in self._prompts:
            raise KeyError(f"No active prompt found for key '{key}'. Available: {list(self._prompts)}")
        return self._prompts[key]

    def get_version(self, key: str, version: str) -> PromptDefinition:
        versioned_key = f"{key}:{version}"
        if versioned_key not in self._prompts:
            raise KeyError(f"Prompt '{key}' version '{version}' not found.")
        return self._prompts[versioned_key]

    def render(self, key: str, variables: dict[str, Any]) -> tuple[str, str]:
        """Returns (rendered_system, rendered_user) for a prompt key."""
        defn = self.get(key)
        system = defn.system
        user = defn.user
        for var_name, value in variables.items():
            placeholder = f"{{{{{var_name}}}}}"
            system = system.replace(placeholder, str(value))
            user = user.replace(placeholder, str(value))
        return system, user

    @property
    def available_keys(self) -> list[str]:
        return [k for k in self._prompts if ":" not in k]


_registry: PromptRegistry | None = None


def get_prompt_registry() -> PromptRegistry:
    global _registry
    if _registry is None:
        _registry = PromptRegistry()
    return _registry
