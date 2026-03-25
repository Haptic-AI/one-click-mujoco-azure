"""Unit tests for simup/config.py."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

import simup.config as config_mod
from simup.config import MjCloudConfig, PRESETS


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point CONFIG_DIR/CONFIG_FILE at tmp_path and clear env vars."""
    cfg_dir = tmp_path / ".simup"
    cfg_file = cfg_dir / "config.yaml"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", cfg_file)
    for var in [
        "SIMUP_SUBSCRIPTION", "SIMUP_RESOURCE_GROUP", "SIMUP_LOCATION",
        "AZURE_SUBSCRIPTION_ID",
    ]:
        monkeypatch.delenv(var, raising=False)


def _write_config(tmp_path: Path, data: dict[str, Any]) -> None:
    cfg_dir = tmp_path / ".simup"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg_dir / "config.yaml", "w") as f:
        yaml.dump(data, f)


def test_defaults() -> None:
    cfg = MjCloudConfig()
    assert cfg.subscription_id == ""
    assert cfg.resource_group == ""
    assert cfg.location == "westus2"
    assert cfg.vm_size == "Standard_NC8as_T4_v3"
    assert cfg.disk_size_gb == 100
    assert cfg.admin_username == "azureuser"
    assert cfg.ssh_key_path == ""


def test_load_defaults_no_file_no_env() -> None:
    cfg = MjCloudConfig.load()
    assert cfg.location == "westus2"
    assert cfg.subscription_id == ""


def test_load_from_yaml(tmp_path: Path) -> None:
    _write_config(tmp_path, {
        "subscription_id": "sub-123",
        "resource_group": "my-rg",
        "location": "eastus",
        "disk_size_gb": 200,
    })
    cfg = MjCloudConfig.load()
    assert cfg.subscription_id == "sub-123"
    assert cfg.resource_group == "my-rg"
    assert cfg.location == "eastus"
    assert cfg.disk_size_gb == 200


def test_load_partial_yaml(tmp_path: Path) -> None:
    _write_config(tmp_path, {"subscription_id": "sub-only"})
    cfg = MjCloudConfig.load()
    assert cfg.subscription_id == "sub-only"
    assert cfg.location == "westus2"


def test_load_empty_yaml(tmp_path: Path) -> None:
    _write_config(tmp_path, {})
    cfg = MjCloudConfig.load()
    assert cfg.subscription_id == ""


def test_load_unknown_keys_ignored(tmp_path: Path) -> None:
    _write_config(tmp_path, {"bogus_key": 123, "subscription_id": "sub-x"})
    cfg = MjCloudConfig.load()
    assert cfg.subscription_id == "sub-x"
    assert not hasattr(cfg, "bogus_key")


def test_env_var_simup_subscription(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path, {"subscription_id": "from-file"})
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "from-env")
    cfg = MjCloudConfig.load()
    assert cfg.subscription_id == "from-env"


def test_env_var_simup_resource_group(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "env-rg")
    cfg = MjCloudConfig.load()
    assert cfg.resource_group == "env-rg"


def test_env_var_simup_location(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIMUP_LOCATION", "northeurope")
    cfg = MjCloudConfig.load()
    assert cfg.location == "northeurope"


def test_env_var_azure_subscription_id_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "azure-sub")
    cfg = MjCloudConfig.load()
    assert cfg.subscription_id == "azure-sub"


def test_env_var_simup_beats_azure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "simup-sub")
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "azure-sub")
    cfg = MjCloudConfig.load()
    assert cfg.subscription_id == "simup-sub"


def test_save_creates_dir_and_file(tmp_path: Path) -> None:
    cfg = MjCloudConfig(subscription_id="save-test", resource_group="rg")
    cfg.save()
    cfg_file = tmp_path / ".simup" / "config.yaml"
    assert cfg_file.exists()


def test_save_load_roundtrip(tmp_path: Path) -> None:
    cfg = MjCloudConfig(subscription_id="rt-sub", resource_group="rt-rg", location="westeurope", disk_size_gb=50)
    cfg.save()
    loaded = MjCloudConfig.load()
    assert loaded.subscription_id == "rt-sub"
    assert loaded.location == "westeurope"
    assert loaded.disk_size_gb == 50


def test_presets_have_required_keys() -> None:
    for name, preset in PRESETS.items():
        for key in ("vm_size", "gpu_count", "gpu_type", "description"):
            assert key in preset, f"{name} missing {key}"


def test_preset_names() -> None:
    expected = {"small", "medium", "large", "v100", "a100", "h100", "cpu"}
    assert set(PRESETS.keys()) == expected


def test_load_null_yaml(tmp_path: Path) -> None:
    """YAML file containing literal 'null' should not crash."""
    cfg_dir = tmp_path / ".simup"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.yaml").write_text("null\n")
    cfg = MjCloudConfig.load()
    assert cfg.subscription_id == ""
