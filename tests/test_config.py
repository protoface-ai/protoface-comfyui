from pathlib import Path

import pytest

from protoface_comfyui.config import Config


@pytest.fixture(autouse=True)
def isolated_operator_settings(monkeypatch):
    import os

    for key in tuple(os.environ):
        if key.startswith("PROTOFACE_"):
            monkeypatch.delenv(key)


def test_environment_settings_and_credential_namespace(tmp_path, monkeypatch):
    settings = {
        "PROTOFACE_STATE_DIR": str(tmp_path),
        "PROTOFACE_API_KEY": "synthetic-env-key",
        "PROTOFACE_BASE_URL": "https://proxy.example.com/",
        "PROTOFACE_CONCURRENCY": "4",
        "PROTOFACE_JOB_TIMEOUT_SECONDS": "321",
        "PROTOFACE_POLL_INTERVAL_SECONDS": "0.5",
        "PROTOFACE_MAX_DOWNLOAD_BYTES": "123456",
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    expected = Config(tmp_path, "https://proxy.example.com", "synthetic-env-key", 4, 321, 0.5, 123456)
    result = Config.from_env()
    assert result == expected
    assert result.namespace == expected.namespace


def test_key_file_fallback_and_environment_precedence(tmp_path, monkeypatch):
    path = tmp_path / "synthetic-key"
    path.write_text("  synthetic-file-key\n")
    monkeypatch.setenv("PROTOFACE_API_KEY_FILE", str(path))
    assert Config.from_env().api_key == "synthetic-file-key"
    monkeypatch.setenv("PROTOFACE_API_KEY", "synthetic-env-key")
    assert Config.from_env().api_key == "synthetic-env-key"


def test_default_settings(monkeypatch):
    config = Config.from_env()
    assert config == Config(Path("~/.cache/protoface-comfyui").expanduser())
    with pytest.raises(ValueError, match="Set PROTOFACE_API_KEY"):
        _ = config.namespace


@pytest.mark.parametrize(
    "origin",
    [
        "http://api.example.com",
        "https://user:password@example.com",
        "file:///tmp/local-path",
        "https://example.com?key=synthetic",
        "https://example.com#fragment",
    ],
)
def test_unsafe_origins_remain_rejected(origin, monkeypatch):
    monkeypatch.setenv("PROTOFACE_BASE_URL", origin)
    with pytest.raises(ValueError):
        Config.from_env()


@pytest.mark.parametrize(
    "key,value",
    [
        ("PROTOFACE_JOB_TIMEOUT_SECONDS", "0"),
        ("PROTOFACE_JOB_TIMEOUT_SECONDS", "nan"),
        ("PROTOFACE_POLL_INTERVAL_SECONDS", "0.1"),
        ("PROTOFACE_POLL_INTERVAL_SECONDS", "inf"),
        ("PROTOFACE_MAX_DOWNLOAD_BYTES", "0"),
    ],
)
def test_invalid_numeric_settings_remain_rejected(key, value, monkeypatch):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        Config.from_env()


def test_local_http_and_concurrency_floor(monkeypatch):
    monkeypatch.setenv("PROTOFACE_BASE_URL", "http://127.0.0.1:8190")
    monkeypatch.setenv("PROTOFACE_CONCURRENCY", "0")
    config = Config.from_env()
    assert config.base_url == "http://127.0.0.1:8190"
    assert config.concurrency == 1
