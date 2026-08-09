"""Config-loader tests — one loader, one precedence chain.

Precedence (highest wins): flags > env (GRAPH_HOST/GRAPH_PORT, DEFAULT_GRAPH,
ANTHROPIC_API_KEY) > ~/.loom/config.json > defaults. Group/world-readable
config warns on stderr. Invalid config files are ignored (silent-continue).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from theloom.config import LoomConfigError, load_config


def write_config(tmp_path: Path, data: dict[str, object], mode: int = 0o600) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    path.chmod(mode)
    return path


def test_defaults_when_no_file_no_env(tmp_path: Path) -> None:
    cfg = load_config(config_path=tmp_path / "missing.json", env={})
    assert cfg.host == "localhost"
    assert cfg.port == 6379
    assert cfg.default_graph == "default"
    assert cfg.anthropic_api_key is None
    assert cfg.model_cache_dir == str(Path.home() / ".loom" / "models")


def test_config_file_values_are_read(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "graphHost": "graph.internal",
            "graphPort": 7000,
            "defaultGraph": "research",
            "anthropicApiKey": "sk-ant-test",
        },
    )
    cfg = load_config(config_path=path, env={})
    assert cfg.host == "graph.internal"
    assert cfg.port == 7000
    assert cfg.default_graph == "research"
    assert cfg.anthropic_api_key == "sk-ant-test"


def test_env_overrides_config_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"graphHost": "from-file", "defaultGraph": "file-graph"})
    cfg = load_config(
        config_path=path,
        env={
            "GRAPH_HOST": "from-env",
            "GRAPH_PORT": "6380",
            "DEFAULT_GRAPH": "env-graph",
            "ANTHROPIC_API_KEY": "sk-ant-env",
        },
    )
    assert cfg.host == "from-env"
    assert cfg.port == 6380
    assert cfg.default_graph == "env-graph"
    assert cfg.anthropic_api_key == "sk-ant-env"


def test_flags_override_env_and_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"graphHost": "from-file"})
    cfg = load_config(
        flags={"host": "from-flag", "default_graph": "flag-graph"},
        config_path=path,
        env={"GRAPH_HOST": "from-env", "DEFAULT_GRAPH": "env-graph"},
    )
    assert cfg.host == "from-flag"
    assert cfg.default_graph == "flag-graph"


def test_invalid_config_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json")
    path.chmod(0o600)
    cfg = load_config(config_path=path, env={})
    assert cfg.default_graph == "default"  # silent fallback


@pytest.mark.parametrize("mode", [0o644, 0o640])
def test_permissive_config_file_warns_on_stderr(
    tmp_path: Path, mode: int, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_config(tmp_path, {"defaultGraph": "x"}, mode=mode)
    load_config(config_path=path, env={})
    stderr = capsys.readouterr().err
    warning = json.loads(stderr)
    assert "warning" in warning
    assert f"chmod 600 {path}" in warning["warning"]


def test_private_config_file_does_not_warn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_config(tmp_path, {"defaultGraph": "x"}, mode=0o600)
    load_config(config_path=path, env={})
    assert capsys.readouterr().err == ""


def test_invalid_port_raises_typed_config_error(tmp_path: Path) -> None:
    with pytest.raises(LoomConfigError) as excinfo:
        load_config(config_path=tmp_path / "missing.json", env={"GRAPH_PORT": "not-a-port"})
    assert excinfo.value.code == "CONFIG_ERROR"


def test_model_cache_dir_from_config_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"modelCacheDir": "/opt/loom/models"})
    cfg = load_config(config_path=path, env={})
    assert cfg.model_cache_dir == "/opt/loom/models"


def test_model_cache_dir_env_overrides_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"modelCacheDir": "/opt/loom/models"})
    cfg = load_config(config_path=path, env={"LOOM_MODEL_CACHE_DIR": "/env/models"})
    assert cfg.model_cache_dir == "/env/models"


def test_model_cache_dir_flag_overrides_env_and_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"modelCacheDir": "/opt/loom/models"})
    cfg = load_config(
        flags={"model_cache_dir": "/flag/models"},
        config_path=path,
        env={"LOOM_MODEL_CACHE_DIR": "/env/models"},
    )
    assert cfg.model_cache_dir == "/flag/models"


# =============================================================================
# Calibration (desire 14): defaultSession, calibrationGapThreshold,
# calibrationMinBucketN
# =============================================================================


def test_calibration_defaults_when_no_file_no_env(tmp_path: Path) -> None:
    cfg = load_config(config_path=tmp_path / "missing.json", env={})
    assert cfg.default_session == "unattributed"
    assert cfg.calibration_gap_threshold == 0.2
    assert cfg.calibration_min_bucket_n == 5


def test_calibration_values_from_config_file(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "defaultSession": "house-author",
            "calibrationGapThreshold": 0.35,
            "calibrationMinBucketN": 8,
        },
    )
    cfg = load_config(config_path=path, env={})
    assert cfg.default_session == "house-author"
    assert cfg.calibration_gap_threshold == 0.35
    assert cfg.calibration_min_bucket_n == 8


def test_calibration_env_overrides_config_file(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "defaultSession": "house-author",
            "calibrationGapThreshold": 0.35,
            "calibrationMinBucketN": 8,
        },
    )
    cfg = load_config(
        config_path=path,
        env={
            "LOOM_DEFAULT_SESSION": "env-author",
            "LOOM_CALIBRATION_GAP_THRESHOLD": "0.15",
            "LOOM_CALIBRATION_MIN_BUCKET_N": "3",
        },
    )
    assert cfg.default_session == "env-author"
    assert cfg.calibration_gap_threshold == 0.15
    assert cfg.calibration_min_bucket_n == 3


def test_invalid_calibration_gap_threshold_env_raises_typed_config_error(tmp_path: Path) -> None:
    with pytest.raises(LoomConfigError) as excinfo:
        load_config(
            config_path=tmp_path / "missing.json",
            env={"LOOM_CALIBRATION_GAP_THRESHOLD": "not-a-number"},
        )
    assert excinfo.value.code == "CONFIG_ERROR"


def test_invalid_calibration_min_bucket_n_env_raises_typed_config_error(tmp_path: Path) -> None:
    with pytest.raises(LoomConfigError) as excinfo:
        load_config(
            config_path=tmp_path / "missing.json",
            env={"LOOM_CALIBRATION_MIN_BUCKET_N": "not-a-number"},
        )
    assert excinfo.value.code == "CONFIG_ERROR"
