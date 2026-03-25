"""Unit tests for simup/cli.py — CLI commands via Click CliRunner."""
from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

# Stub azure SDK modules before any simup import can pull them in.
for _mod in (
    "azure",
    "azure.identity",
    "azure.mgmt",
    "azure.mgmt.compute",
    "azure.mgmt.network",
    "azure.mgmt.resource",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from click.testing import CliRunner  # noqa: E402
from simup.cli import cli, get_config, _check_region_quota, _find_best_region  # noqa: E402
from simup.config import MjCloudConfig, PRESETS  # noqa: E402
import simup.cli as cli_mod  # noqa: E402
import simup.config as config_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ENV_VARS = [
    "SIMUP_SUBSCRIPTION",
    "SIMUP_RESOURCE_GROUP",
    "SIMUP_LOCATION",
    "AZURE_SUBSCRIPTION_ID",
]


def _clean_env(monkeypatch) -> None:
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _patch_config_paths(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".simup"
    cfg_file = cfg_dir / "config.yaml"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", cfg_file)
    return cfg_dir, cfg_file


# ---------------------------------------------------------------------------
# presets command
# ---------------------------------------------------------------------------


def test_presets_command() -> None:
    """simup presets exits 0 and output contains all preset names."""
    runner = CliRunner()
    result = runner.invoke(cli, ["presets"])
    assert result.exit_code == 0
    for name in PRESETS:
        assert name in result.output


# ---------------------------------------------------------------------------
# deploy --dry-run
# ---------------------------------------------------------------------------


def test_deploy_dry_run(tmp_path, monkeypatch) -> None:
    """simup deploy --dry-run exits 0, output contains 'Dry Run', does NOT call create_instance."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub-dry")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg-dry")

    with patch.object(cli_mod.azure_vm, "create_instance") as mock_create:
        runner = CliRunner()
        result = runner.invoke(cli, ["deploy", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Dry Run" in result.output
    mock_create.assert_not_called()


def test_deploy_dry_run_with_max_hours(tmp_path, monkeypatch) -> None:
    """--dry-run --max-hours 2 output contains 'Auto-shutdown'."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub-dry")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg-dry")

    runner = CliRunner()
    result = runner.invoke(cli, ["deploy", "--dry-run", "--max-hours", "2"])

    assert result.exit_code == 0, result.output
    assert "Auto-shutdown" in result.output


# ---------------------------------------------------------------------------
# config command
# ---------------------------------------------------------------------------


def test_config_no_args_shows_defaults(tmp_path, monkeypatch) -> None:
    """simup config with no args shows current config values."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub-show")

    runner = CliRunner()
    result = runner.invoke(cli, ["config"])

    assert result.exit_code == 0, result.output
    assert "sub-show" in result.output
    assert "westus2" in result.output


def test_config_set_subscription(tmp_path, monkeypatch) -> None:
    """simup config --subscription ... calls save."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)

    with patch.object(MjCloudConfig, "save") as mock_save:
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "--subscription", "new-sub"])

    assert result.exit_code == 0, result.output
    mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# get_config validation
# ---------------------------------------------------------------------------


def test_get_config_missing_subscription_exits(tmp_path, monkeypatch) -> None:
    """get_config() with no subscription configured should SystemExit."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg-ok")

    with pytest.raises(SystemExit):
        get_config()


def test_get_config_missing_resource_group_exits(tmp_path, monkeypatch) -> None:
    """get_config() with no resource group configured should SystemExit."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub-ok")

    with pytest.raises(SystemExit):
        get_config()


# ---------------------------------------------------------------------------
# _check_region_quota
# ---------------------------------------------------------------------------


def test_check_region_quota_unknown_vm_size() -> None:
    """Unknown VM size returns None."""
    result = _check_region_quota("eastus", "Unknown_Size")
    assert result is None


def test_check_region_quota_az_fails() -> None:
    """subprocess.run returns non-zero -> returns None."""
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""

    with patch("simup.cli.subprocess.run", return_value=mock_result):
        result = _check_region_quota("eastus", "Standard_NC8as_T4_v3")
    assert result is None


def test_check_region_quota_parses_json() -> None:
    """Parses quota JSON and returns correct available count."""
    quota_json = json.dumps([
        {
            "name": {"value": "Standard NCASv3_T4 Family"},
            "limit": 24,
            "currentValue": 8,
        },
        {
            "name": {"value": "Other Family"},
            "limit": 100,
            "currentValue": 50,
        },
    ])
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = quota_json

    with patch("simup.cli.subprocess.run", return_value=mock_result):
        available = _check_region_quota("eastus", "Standard_NC8as_T4_v3")
    assert available == 16


# ---------------------------------------------------------------------------
# _find_best_region
# ---------------------------------------------------------------------------


def test_find_best_region_current_has_quota() -> None:
    """Config.location has quota -> returns None (no switch needed)."""
    cfg = MjCloudConfig(subscription_id="s", resource_group="rg", location="westus2")
    preset_info = PRESETS["medium"]

    with patch("simup.cli._check_region_quota", return_value=8):
        result = _find_best_region(cfg, "medium", preset_info)
    assert result is None


def test_find_best_region_fallback() -> None:
    """Current region 0 quota, alternative region has quota -> returns alt region."""
    cfg = MjCloudConfig(subscription_id="s", resource_group="rg", location="westus2")
    preset_info = PRESETS["medium"]

    def mock_quota(region, vm_size):
        if region == "westus2":
            return 0
        if region == "eastus":
            return 4
        return 0

    with patch("simup.cli._check_region_quota", side_effect=mock_quota):
        result = _find_best_region(cfg, "medium", preset_info)
    assert result == "eastus"


def test_find_best_region_none_available() -> None:
    """All regions return 0 -> returns None."""
    cfg = MjCloudConfig(subscription_id="s", resource_group="rg", location="westus2")
    preset_info = PRESETS["medium"]

    with patch("simup.cli._check_region_quota", return_value=0):
        result = _find_best_region(cfg, "medium", preset_info)
    assert result is None


# ---------------------------------------------------------------------------
# destroy command
# ---------------------------------------------------------------------------


def test_destroy_command(tmp_path, monkeypatch) -> None:
    """simup destroy -y calls delete_instance."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub-del")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg-del")

    with patch.object(cli_mod.azure_vm, "delete_instance") as mock_delete:
        runner = CliRunner()
        result = runner.invoke(cli, ["destroy", "test-vm", "-y"])

    assert result.exit_code == 0, result.output
    mock_delete.assert_called_once()
    # Verify it was called with the right VM name
    _, call_kwargs = mock_delete.call_args
    if not call_kwargs:
        call_args = mock_delete.call_args[0]
        assert call_args[1] == "test-vm"


# ---------------------------------------------------------------------------
# list command
# ---------------------------------------------------------------------------


def test_list_command_empty(tmp_path, monkeypatch) -> None:
    """list_instances returns [] -> output contains 'No simup VMs'."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub-list")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg-list")

    with patch.object(cli_mod.azure_vm, "list_instances", return_value=[]):
        runner = CliRunner()
        result = runner.invoke(cli, ["list"])

    assert result.exit_code == 0, result.output
    assert "No simup VMs" in result.output


# ---------------------------------------------------------------------------
# list command — with VMs
# ---------------------------------------------------------------------------


def test_list_command_with_vms(tmp_path, monkeypatch) -> None:
    """list_instances returns VMs -> output contains VM names."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub-list")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg-list")

    vms = [
        {"name": "simup-aaa", "status": "RUNNING", "external_ip": "1.2.3.4", "vm_size": "Standard_D4s_v3"},
        {"name": "simup-bbb", "status": "STOPPED", "external_ip": None, "vm_size": "Standard_NC8as_T4_v3"},
    ]
    with patch.object(cli_mod.azure_vm, "list_instances", return_value=vms):
        runner = CliRunner()
        result = runner.invoke(cli, ["list"])

    assert result.exit_code == 0, result.output
    assert "simup-aaa" in result.output
    assert "simup-bbb" in result.output


# ---------------------------------------------------------------------------
# deploy (non-dry-run)
# ---------------------------------------------------------------------------


def test_deploy_full(tmp_path, monkeypatch) -> None:
    """simup deploy calls create_instance and wait_for_running."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub-dep")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg-dep")

    vm_info = {"name": "simup-abc", "status": "RUNNING", "external_ip": "1.2.3.4", "vm_size": "Standard_D4s_v3"}

    with patch.object(cli_mod.azure_vm, "create_instance", return_value=vm_info) as mock_create, \
         patch.object(cli_mod.azure_vm, "wait_for_running", return_value=vm_info), \
         patch("simup.cli._find_best_region", return_value=None):
        runner = CliRunner()
        result = runner.invoke(cli, ["deploy", "--preset", "cpu"])

    assert result.exit_code == 0, result.output
    assert "simup-abc" in result.output
    mock_create.assert_called_once()


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------


def test_status_command_ready(tmp_path, monkeypatch) -> None:
    """simup status shows VM info and setup status when SSH returns READY."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub-st")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg-st")

    vm_info = {"name": "simup-xyz", "status": "RUNNING", "external_ip": "5.6.7.8",
               "vm_size": "Standard_D4s_v3", "location": "eastus"}

    ssh_result = MagicMock()
    ssh_result.returncode = 0
    ssh_result.stdout = "READY\n"

    with patch.object(cli_mod.azure_vm, "get_instance", return_value=vm_info), \
         patch("simup.cli._ssh_command", return_value=ssh_result):
        runner = CliRunner()
        result = runner.invoke(cli, ["status", "simup-xyz"])

    assert result.exit_code == 0, result.output
    assert "simup-xyz" in result.output
    assert "5.6.7.8" in result.output
    assert "Ready" in result.output


def test_status_command_setting_up(tmp_path, monkeypatch) -> None:
    """simup status shows 'Setting up' when SSH returns SETTING_UP."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub-st")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg-st")

    vm_info = {"name": "simup-xyz", "status": "RUNNING", "external_ip": "5.6.7.8",
               "vm_size": "Standard_D4s_v3", "location": "eastus"}

    ssh_result = MagicMock()
    ssh_result.returncode = 0
    ssh_result.stdout = "SETTING_UP\n"

    with patch.object(cli_mod.azure_vm, "get_instance", return_value=vm_info), \
         patch("simup.cli._ssh_command", return_value=ssh_result):
        runner = CliRunner()
        result = runner.invoke(cli, ["status", "simup-xyz"])

    assert result.exit_code == 0, result.output
    assert "Setting up" in result.output


def test_status_command_ssh_unreachable(tmp_path, monkeypatch) -> None:
    """simup status shows 'Unknown' when SSH raises exception."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub-st")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg-st")

    vm_info = {"name": "simup-xyz", "status": "RUNNING", "external_ip": "5.6.7.8",
               "vm_size": "Standard_D4s_v3", "location": "eastus"}

    with patch.object(cli_mod.azure_vm, "get_instance", return_value=vm_info), \
         patch("simup.cli._ssh_command", side_effect=Exception("timeout")):
        runner = CliRunner()
        result = runner.invoke(cli, ["status", "simup-xyz"])

    assert result.exit_code == 0, result.output
    assert "Unknown" in result.output


def test_status_command_vm_not_found(tmp_path, monkeypatch) -> None:
    """simup status exits 1 when VM doesn't exist."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub-st")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg-st")

    with patch.object(cli_mod.azure_vm, "get_instance", side_effect=Exception("not found")):
        runner = CliRunner()
        result = runner.invoke(cli, ["status", "no-such-vm"])

    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# ssh command
# ---------------------------------------------------------------------------


def test_ssh_command_no_ip(tmp_path, monkeypatch) -> None:
    """simup ssh exits 1 when VM has no public IP."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg")

    vm_info = {"name": "vm1", "status": "RUNNING", "external_ip": None,
               "vm_size": "Standard_D4s_v3", "location": "eastus"}

    with patch.object(cli_mod.azure_vm, "get_instance", return_value=vm_info):
        runner = CliRunner()
        result = runner.invoke(cli, ["ssh", "vm1"])

    assert result.exit_code == 1
    assert "no public IP" in result.output


def test_ssh_command_calls_execvp(tmp_path, monkeypatch) -> None:
    """simup ssh calls os.execvp with correct SSH args."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg")

    vm_info = {"name": "vm1", "status": "RUNNING", "external_ip": "9.8.7.6",
               "vm_size": "Standard_D4s_v3", "location": "eastus"}

    with patch.object(cli_mod.azure_vm, "get_instance", return_value=vm_info), \
         patch("simup.cli.os.execvp") as mock_execvp:
        runner = CliRunner()
        runner.invoke(cli, ["ssh", "vm1"])

    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0]
    assert args[0] == "ssh"
    assert "azureuser@9.8.7.6" in args[1]


# ---------------------------------------------------------------------------
# jupyter command
# ---------------------------------------------------------------------------


def test_jupyter_with_token(tmp_path, monkeypatch) -> None:
    """simup jupyter shows URL with token when SSH returns one."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg")

    vm_info = {"name": "vm1", "status": "RUNNING", "external_ip": "1.2.3.4",
               "vm_size": "Standard_D4s_v3", "location": "eastus"}

    ssh_result = MagicMock()
    ssh_result.returncode = 0
    ssh_result.stdout = "abc123token\n"

    with patch.object(cli_mod.azure_vm, "get_instance", return_value=vm_info), \
         patch("simup.cli._ssh_command", return_value=ssh_result):
        runner = CliRunner()
        result = runner.invoke(cli, ["jupyter", "vm1"])

    assert result.exit_code == 0, result.output
    assert "abc123token" in result.output
    assert "http://1.2.3.4:8888" in result.output


def test_jupyter_no_token(tmp_path, monkeypatch) -> None:
    """simup jupyter shows base URL when token retrieval fails."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg")

    vm_info = {"name": "vm1", "status": "RUNNING", "external_ip": "1.2.3.4",
               "vm_size": "Standard_D4s_v3", "location": "eastus"}

    with patch.object(cli_mod.azure_vm, "get_instance", return_value=vm_info), \
         patch("simup.cli._ssh_command", side_effect=Exception("fail")):
        runner = CliRunner()
        result = runner.invoke(cli, ["jupyter", "vm1"])

    assert result.exit_code == 0, result.output
    assert "http://1.2.3.4:8888" in result.output
    assert "setup may still be in progress" in result.output


def test_jupyter_no_ip(tmp_path, monkeypatch) -> None:
    """simup jupyter exits 1 when VM has no public IP."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg")

    vm_info = {"name": "vm1", "status": "RUNNING", "external_ip": None,
               "vm_size": "Standard_D4s_v3", "location": "eastus"}

    with patch.object(cli_mod.azure_vm, "get_instance", return_value=vm_info):
        runner = CliRunner()
        result = runner.invoke(cli, ["jupyter", "vm1"])

    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# stop command
# ---------------------------------------------------------------------------


def test_stop_command_success(tmp_path, monkeypatch) -> None:
    """simup stop -y calls az vm deallocate."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg")

    mock_result = MagicMock()
    mock_result.returncode = 0

    with patch("simup.cli.subprocess.run", return_value=mock_result) as mock_run:
        runner = CliRunner()
        result = runner.invoke(cli, ["stop", "vm1", "-y"])

    assert result.exit_code == 0, result.output
    assert "deallocated" in result.output
    # Verify az vm deallocate was called
    call_args = mock_run.call_args[0][0]
    assert "deallocate" in call_args


def test_stop_command_failure(tmp_path, monkeypatch) -> None:
    """simup stop -y exits 1 when az command fails."""
    import subprocess as sp
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg")

    with patch("simup.cli.subprocess.run", side_effect=sp.CalledProcessError(1, "az", stderr="quota")):
        runner = CliRunner()
        result = runner.invoke(cli, ["stop", "vm1", "-y"])

    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# download command
# ---------------------------------------------------------------------------


def test_download_command_success(tmp_path, monkeypatch) -> None:
    """simup download calls scp and succeeds."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg")

    vm_info = {"name": "vm1", "status": "RUNNING", "external_ip": "1.2.3.4",
               "vm_size": "Standard_D4s_v3", "location": "eastus"}

    scp_result = MagicMock()
    scp_result.returncode = 0

    with patch.object(cli_mod.azure_vm, "get_instance", return_value=vm_info), \
         patch("simup.cli.subprocess.run", return_value=scp_result):
        runner = CliRunner()
        result = runner.invoke(cli, ["download", "vm1", "/tmp/video.mp4", "./"])

    assert result.exit_code == 0, result.output
    assert "Downloaded" in result.output


def test_download_command_no_ip(tmp_path, monkeypatch) -> None:
    """simup download exits 1 when VM has no public IP."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg")

    vm_info = {"name": "vm1", "status": "RUNNING", "external_ip": None,
               "vm_size": "Standard_D4s_v3", "location": "eastus"}

    with patch.object(cli_mod.azure_vm, "get_instance", return_value=vm_info):
        runner = CliRunner()
        result = runner.invoke(cli, ["download", "vm1", "/tmp/video.mp4", "./"])

    assert result.exit_code == 1


def test_download_command_scp_fails(tmp_path, monkeypatch) -> None:
    """simup download exits 1 when scp fails."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "sub")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "rg")

    vm_info = {"name": "vm1", "status": "RUNNING", "external_ip": "1.2.3.4",
               "vm_size": "Standard_D4s_v3", "location": "eastus"}

    scp_result = MagicMock()
    scp_result.returncode = 1

    with patch.object(cli_mod.azure_vm, "get_instance", return_value=vm_info), \
         patch("simup.cli.subprocess.run", return_value=scp_result):
        runner = CliRunner()
        result = runner.invoke(cli, ["download", "vm1", "/tmp/video.mp4", "./"])

    assert result.exit_code == 1
    assert "Download failed" in result.output


# ---------------------------------------------------------------------------
# get_config with CLI overrides
# ---------------------------------------------------------------------------


def test_get_config_overrides(tmp_path, monkeypatch) -> None:
    """get_config applies CLI overrides over loaded config."""
    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)
    monkeypatch.setenv("SIMUP_SUBSCRIPTION", "base-sub")
    monkeypatch.setenv("SIMUP_RESOURCE_GROUP", "base-rg")

    cfg = get_config(subscription="override-sub", location="northeurope")
    assert cfg.subscription_id == "override-sub"
    assert cfg.location == "northeurope"
    assert cfg.resource_group == "base-rg"


# ---------------------------------------------------------------------------
# _schedule_auto_shutdown
# ---------------------------------------------------------------------------


def test_schedule_auto_shutdown_success(tmp_path, monkeypatch) -> None:
    """_schedule_auto_shutdown calls az with correct --time format."""
    from simup.cli import _schedule_auto_shutdown

    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)

    mock_result = MagicMock()
    mock_result.returncode = 0

    cfg = MjCloudConfig(subscription_id="s", resource_group="rg")

    with patch("simup.cli.subprocess.run", return_value=mock_result) as mock_run:
        _schedule_auto_shutdown(cfg, "vm1", 2.0)

    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert "auto-shutdown" in call_args
    assert "--time" in call_args


def test_schedule_auto_shutdown_failure(tmp_path, monkeypatch) -> None:
    """_schedule_auto_shutdown prints warning on failure, doesn't crash."""
    from simup.cli import _schedule_auto_shutdown

    _patch_config_paths(tmp_path, monkeypatch)
    _clean_env(monkeypatch)

    cfg = MjCloudConfig(subscription_id="s", resource_group="rg")

    with patch("simup.cli.subprocess.run", side_effect=Exception("az not found")):
        # Should not raise
        _schedule_auto_shutdown(cfg, "vm1", 2.0)
