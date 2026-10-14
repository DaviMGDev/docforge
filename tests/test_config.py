"""Unit and integration tests for persistent XDG configuration and precedence resolution (Issue #16)."""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from docforge.cli import app
from docforge.config import (
    DefaultSettings,
    LlmSettings,
    UserSettings,
    find_settings_file,
    get_candidate_settings_paths,
    get_default_settings_template,
    load_user_settings,
    resolve_value_precedence,
)


@pytest.fixture
def dummy_pdf(tmp_path: Path) -> Path:
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 sample")
    return pdf_file


def test_xdg_candidate_paths_resolution(tmp_path: Path):
    with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path)}):
        paths = get_candidate_settings_paths()
        assert paths[0] == tmp_path / "docforge" / "settings.json"
        assert paths[1] == tmp_path / "pdf2md" / "settings.json"

    with patch.dict(os.environ, {}, clear=True):
        paths = get_candidate_settings_paths()
        assert paths[0] == Path.home() / ".config" / "docforge" / "settings.json"
        assert paths[1] == Path.home() / ".config" / "pdf2md" / "settings.json"


def test_find_settings_file_precedence(tmp_path: Path):
    docforge_cfg = tmp_path / "docforge" / "settings.json"
    pdf2md_cfg = tmp_path / "pdf2md" / "settings.json"

    with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path)}):
        # When neither exists
        assert find_settings_file() is None

        # When legacy pdf2md exists
        pdf2md_cfg.parent.mkdir(parents=True)
        pdf2md_cfg.write_text("{}")
        assert find_settings_file() == pdf2md_cfg

        # When docforge exists, it takes precedence over pdf2md
        docforge_cfg.parent.mkdir(parents=True)
        docforge_cfg.write_text("{}")
        assert find_settings_file() == docforge_cfg


def test_load_user_settings_missing_file():
    settings = load_user_settings("/nonexistent/file/path/settings.json")
    assert isinstance(settings, UserSettings)
    assert settings.llm.model_name is None
    assert settings.defaults.chunk_size is None


def test_load_user_settings_empty_file(tmp_path: Path):
    empty_file = tmp_path / "settings.json"
    empty_file.write_text("")
    settings = load_user_settings(empty_file)
    assert isinstance(settings, UserSettings)
    assert settings.llm.model_name is None


def test_load_user_settings_invalid_json(tmp_path: Path):
    corrupt_file = tmp_path / "settings.json"
    corrupt_file.write_text("{ malformed: json, ")
    settings = load_user_settings(corrupt_file)
    assert isinstance(settings, UserSettings)
    assert settings.llm.model_name is None


def test_load_user_settings_valid_json(tmp_path: Path):
    valid_file = tmp_path / "settings.json"
    payload = {
        "llm": {
            "model_name": "ollama/gemma4:cloud",
            "api_base": "http://localhost:11434",
            "max_tokens": 16384,
            "context_window": 65536,
            "timeout_seconds": 180.0,
        },
        "defaults": {
            "format": ["md", "json"],
            "page_markers": "badge",
            "generate_toc": False,
            "chunk_size": 2,
            "llm_concurrency": 2,
            "cache_dir": "~/.cache/custom",
            "instructions": "Transcribe accurately.",
            "prompt_file": "SYSTEM.md",
            "split_by": "chapter",
            "split_pattern": "{index:02d}_{slug}.md",
        },
    }
    valid_file.write_text(json.dumps(payload))

    settings = load_user_settings(valid_file)
    assert settings.llm.model_name == "ollama/gemma4:cloud"
    assert settings.llm.max_tokens == 16384
    assert settings.llm.context_window == 65536
    assert settings.llm.timeout_seconds == 180.0
    assert settings.defaults.format == ["md", "json"]
    assert settings.defaults.page_markers == "badge"
    assert settings.defaults.generate_toc is False
    assert settings.defaults.chunk_size == 2
    assert settings.defaults.instructions == "Transcribe accurately."
    assert settings.defaults.prompt_file == "SYSTEM.md"
    assert settings.defaults.split_by == "chapter"
    assert settings.defaults.split_pattern == "{index:02d}_{slug}.md"


def test_default_template_generation():
    template = get_default_settings_template()
    assert "llm" in template
    assert "defaults" in template
    assert template["llm"]["model_name"] == "ollama/gemma4:cloud"
    assert template["defaults"]["chunk_size"] == 4
    assert template["defaults"]["prompt_file"] == "SYSTEM.md"


def test_five_tier_precedence_resolution():
    # 1. Tier 1: CLI wins over all
    res1 = resolve_value_precedence(
        cli_value="cli_val",
        env_keys=["TEST_KEY"],
        settings_value="settings_val",
        default_value="default_val",
        env_dict={"TEST_KEY": "env_val"},
        dotenv_dict={"TEST_KEY": "dotenv_val"},
    )
    assert res1 == "cli_val"

    # 2. Tier 2: Process environment wins over .env, settings, and defaults
    res2 = resolve_value_precedence(
        cli_value=None,
        env_keys=["TEST_KEY"],
        settings_value="settings_val",
        default_value="default_val",
        env_dict={"TEST_KEY": "env_val"},
        dotenv_dict={"TEST_KEY": "dotenv_val"},
    )
    assert res2 == "env_val"

    # 3. Tier 3: Local .env wins over settings and defaults
    res3 = resolve_value_precedence(
        cli_value=None,
        env_keys=["TEST_KEY"],
        settings_value="settings_val",
        default_value="default_val",
        env_dict={},
        dotenv_dict={"TEST_KEY": "dotenv_val"},
    )
    assert res3 == "dotenv_val"

    # 4. Tier 4: settings.json wins over defaults
    res4 = resolve_value_precedence(
        cli_value=None,
        env_keys=["TEST_KEY"],
        settings_value="settings_val",
        default_value="default_val",
        env_dict={},
        dotenv_dict={},
    )
    assert res4 == "settings_val"

    # 5. Tier 5: Built-in default is used when nothing else is supplied
    res5 = resolve_value_precedence(
        cli_value=None,
        env_keys=["TEST_KEY"],
        settings_value=None,
        default_value="default_val",
        env_dict={},
        dotenv_dict={},
    )
    assert res5 == "default_val"


def test_cli_integration_with_persistent_settings(dummy_pdf: Path, tmp_path: Path):
    cfg_dir = tmp_path / "docforge"
    cfg_dir.mkdir(parents=True)
    settings_file = cfg_dir / "settings.json"
    settings_file.write_text(json.dumps({
        "llm": {
            "model_name": "ollama/llama3.2-vision",
            "max_tokens": 4096,
            "context_window": 16384,
            "timeout_seconds": 99.0,
        },
        "defaults": {
            "chunk_size": 2,
            "page_markers": "badge",
            "format": ["md", "typ"],
        },
    }))

    runner = CliRunner()
    mock_doc = MagicMock()
    mock_doc.__len__.return_value = 1
    mock_ast = MagicMock()
    mock_ast.blocks = []

    with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path)}, clear=True):
        with patch("docforge.cli.dotenv_values", return_value={}):
            with patch("docforge.cli.PipelineRunner") as mock_pipeline:
                mock_pipeline.return_value.run = AsyncMock(return_value=(mock_ast, "out.md", None))
                with patch("docforge.cli.open_pdf_document", return_value=mock_doc):
                    result = runner.invoke(app, [str(dummy_pdf)])
                    assert result.exit_code == 0
                    config = mock_pipeline.call_args[0][0]
                    assert config.llm.model_name == "ollama/llama3.2-vision"
                    assert config.llm.max_tokens == 4096
                    assert config.llm.context_window == 16384
                    assert config.llm.request_timeout_seconds == 99.0
                    assert config.page_marker_mode.value == "badge"
                    assert config.output_formats == ["md", "typ"]
