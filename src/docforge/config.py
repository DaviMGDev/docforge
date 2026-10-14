"""Persistent XDG user configuration manager for docforge.

Conforms to Issue #16 and supports hierarchical configuration precedence:
CLI > Env vars > Local .env > ~/.config/<app>/settings.json > Built-in defaults.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

logger = logging.getLogger("docforge.config")


class LlmSettings(BaseModel):
    model_name: Optional[str] = None
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    max_tokens: Optional[int] = None
    context_window: Optional[int] = None
    timeout_seconds: Optional[float] = None
    temperature: Optional[float] = None
    max_retries: Optional[int] = None


class DefaultSettings(BaseModel):
    format: Optional[Union[str, List[str]]] = None
    page_markers: Optional[str] = None
    generate_toc: Optional[bool] = None
    chunk_size: Optional[int] = None
    llm_concurrency: Optional[int] = None
    cache_dir: Optional[str] = None
    dpi: Optional[int] = None
    instructions: Optional[str] = None
    prompt_file: Optional[str] = None


class UserSettings(BaseModel):
    llm: LlmSettings = Field(default_factory=LlmSettings)
    defaults: DefaultSettings = Field(default_factory=DefaultSettings)


def get_candidate_settings_paths() -> List[Path]:
    """Return ordered candidate paths for settings.json according to XDG spec."""
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config and xdg_config.strip():
        base = Path(xdg_config).expanduser()
    else:
        base = Path.home() / ".config"

    return [
        base / "docforge" / "settings.json",
        base / "pdf2md" / "settings.json",
    ]


def find_settings_file() -> Optional[Path]:
    """Find the first existing settings.json candidate file, or None."""
    for path in get_candidate_settings_paths():
        if path.is_file():
            return path
    return None


def get_default_settings_template() -> Dict[str, Any]:
    """Generate default settings dictionary template conforming to Issue #16."""
    return {
        "llm": {
            "model_name": "ollama/gemma4:cloud",
            "api_base": "http://localhost:11434",
            "api_key": None,
            "max_tokens": 8192,
            "context_window": 32768,
            "timeout_seconds": 120.0,
        },
        "defaults": {
            "format": ["md"],
            "page_markers": "anchor",
            "generate_toc": True,
            "chunk_size": 4,
            "llm_concurrency": 3,
            "cache_dir": "~/.cache/docforge",
            "instructions": None,
            "prompt_file": "SYSTEM.md",
        },
    }


def load_user_settings(config_path: Optional[Union[str, Path]] = None) -> UserSettings:
    """Load and parse persistent settings from specified path or standard XDG locations.

    Falls back safely to default UserSettings on missing file or parse errors.
    """
    target_path = Path(config_path) if config_path else find_settings_file()
    if not target_path or not target_path.is_file():
        return UserSettings()

    try:
        content = target_path.read_text(encoding="utf-8").strip()
        if not content:
            # Empty file falls back safely to defaults
            return UserSettings()
        data = json.loads(content)
        if not isinstance(data, dict):
            logger.warning(f"Settings file at {target_path} is not a valid JSON object. Falling back to defaults.")
            return UserSettings()
        return UserSettings.model_validate(data)
    except Exception as e:
        logger.warning(f"Failed to parse settings file at {target_path}: {e}. Falling back to defaults.")
        return UserSettings()


def resolve_value_precedence(
    cli_value: Any = None,
    env_keys: Optional[List[str]] = None,
    settings_value: Any = None,
    default_value: Any = None,
    env_dict: Optional[Dict[str, str]] = None,
    dotenv_dict: Optional[Dict[str, str]] = None,
) -> Any:
    """Resolve a single parameter according to the 5-tier precedence hierarchy:
    Tier 1: Explicit CLI value (highest priority, when not None)
    Tier 2: Environment variables exported in shell/process environment
    Tier 3: Local .env file variables
    Tier 4: User settings from settings.json
    Tier 5: Built-in defaults (lowest priority)
    """
    # Tier 1: CLI
    if cli_value is not None:
        return cli_value

    keys = env_keys or []
    current_env = env_dict if env_dict is not None else dict(os.environ)
    local_dotenv = dotenv_dict if dotenv_dict is not None else {}

    # Tier 2: Shell/process environment
    # If dotenv_dict is tracked, an env var that is NOT identical to dotenv or exists in shell takes Tier 2 precedence
    for k in keys:
        if k in current_env:
            if dotenv_dict is not None and k in local_dotenv and current_env[k] == local_dotenv[k]:
                continue
            return current_env[k]

    # Tier 3: Local .env
    for k in keys:
        if k in local_dotenv:
            return local_dotenv[k]
        elif dotenv_dict is None and k in current_env:
            return current_env[k]

    # Tier 4: settings.json
    if settings_value is not None:
        return settings_value

    # Tier 5: Built-in default
    return default_value

