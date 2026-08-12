import pytest
import yaml

from jobscout.config import AIConfig, load_config


def _write_config(tmp_path, data: dict) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(path)


def test_ai_config_defaults_when_no_ai_block(tmp_path):
    path = _write_config(tmp_path, {"ranking_mode": "heuristic"})
    config = load_config(path)
    assert config.ai == AIConfig()
    assert config.ai.provider == "gemini"
    assert config.ai.model == "gemini-3.5-flash-lite"
    assert config.ai.max_description_chars == 4000
    assert config.ai.max_retries == 3


def test_ai_block_parsed_from_yaml(tmp_path):
    path = _write_config(
        tmp_path,
        {
            "ranking_mode": "ai",
            "ai": {
                "provider": "anthropic",
                "model": "claude-haiku-4-5",
                "max_description_chars": 2000,
                "max_retries": 5,
            },
        },
    )
    config = load_config(path)
    assert config.ranking_mode == "ai"
    assert config.ai.provider == "anthropic"
    assert config.ai.model == "claude-haiku-4-5"
    assert config.ai.max_description_chars == 2000
    assert config.ai.max_retries == 5


def test_invalid_ranking_mode_raises(tmp_path):
    path = _write_config(tmp_path, {"ranking_mode": "bogus"})
    with pytest.raises(ValueError):
        load_config(path)


def test_heuristic_ranking_mode_is_default(tmp_path):
    path = _write_config(tmp_path, {})
    config = load_config(path)
    assert config.ranking_mode == "heuristic"
