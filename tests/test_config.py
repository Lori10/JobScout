import pytest
import yaml

from jobscout.config import AIConfig, BoardRegistryConfig, GitHubListsConfig, GitHubRepoSource, load_config


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


def test_board_registry_config_defaults_when_no_block(tmp_path):
    path = _write_config(tmp_path, {})
    config = load_config(path)
    assert config.board_registry == BoardRegistryConfig()
    assert config.board_registry.enabled is True
    assert config.board_registry.max_workers == 15


def test_board_registry_block_parsed_from_yaml(tmp_path):
    path = _write_config(tmp_path, {"board_registry": {"enabled": False, "max_workers": 5}})
    config = load_config(path)
    assert config.board_registry.enabled is False
    assert config.board_registry.max_workers == 5


def test_github_lists_config_defaults_when_no_block(tmp_path):
    path = _write_config(tmp_path, {})
    config = load_config(path)
    assert config.github_lists == GitHubListsConfig()
    assert config.github_lists.enabled is True
    assert config.github_lists.scan_interval_days == 7
    assert "established-remote" in config.github_lists.sources
    assert [s.name for s in config.github_lists.repo_sources] == ["remoteintech-remote-jobs"]


def test_github_lists_block_parsed_from_yaml(tmp_path):
    path = _write_config(
        tmp_path,
        {
            "github_lists": {
                "enabled": False,
                "scan_interval_days": 14,
                "sources": {"custom-list": "https://example.com/README.md"},
            }
        },
    )
    config = load_config(path)
    assert config.github_lists.enabled is False
    assert config.github_lists.scan_interval_days == 14
    assert config.github_lists.sources == {"custom-list": "https://example.com/README.md"}
    # repo_sources wasn't in the YAML block, so it falls back to the
    # dataclass default, same precedent `sources` already sets.
    assert [s.name for s in config.github_lists.repo_sources] == ["remoteintech-remote-jobs"]


def test_github_lists_repo_sources_parsed_from_yaml(tmp_path):
    path = _write_config(
        tmp_path,
        {
            "github_lists": {
                "sources": {},
                "repo_sources": [
                    {
                        "name": "custom-repo",
                        "owner": "acme",
                        "repo": "jobs",
                        "branch": "trunk",
                        "path_prefix": "companies/",
                        "frontmatter_field": "apply_url",
                    }
                ],
            }
        },
    )
    config = load_config(path)
    assert config.github_lists.repo_sources == [
        GitHubRepoSource(
            name="custom-repo",
            owner="acme",
            repo="jobs",
            branch="trunk",
            path_prefix="companies/",
            frontmatter_field="apply_url",
        )
    ]


def test_github_lists_repo_sources_empty_list_disables_repo_scanning(tmp_path):
    path = _write_config(tmp_path, {"github_lists": {"repo_sources": []}})
    config = load_config(path)
    assert config.github_lists.repo_sources == []
