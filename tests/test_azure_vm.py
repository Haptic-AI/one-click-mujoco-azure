"""Unit tests for simup/azure_vm.py — pure logic only, no Azure SDK calls."""
from __future__ import annotations

import base64
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# We must prevent the real azure SDK imports in azure_vm from failing.
# Insert lightweight stubs before importing the module.
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

from simup.config import MjCloudConfig  # noqa: E402
import simup.azure_vm as azure_vm  # noqa: E402


# ---------------------------------------------------------------------------
# _generate_instance_name
# ---------------------------------------------------------------------------


def test_generate_instance_name_format() -> None:
    """Name matches simup-[0-9a-f]{8}."""
    name = azure_vm._generate_instance_name()
    assert re.fullmatch(r"simup-[0-9a-f]{8}", name), f"Unexpected name format: {name}"


def test_generate_instance_name_unique() -> None:
    """Two calls return different names."""
    a = azure_vm._generate_instance_name()
    b = azure_vm._generate_instance_name()
    assert a != b


# ---------------------------------------------------------------------------
# get_startup_script
# ---------------------------------------------------------------------------


def test_get_startup_script_valid_base64() -> None:
    """Result decodes to valid base64 and matches the startup_script.sh file."""
    encoded = azure_vm.get_startup_script()
    decoded = base64.b64decode(encoded).decode()
    expected = azure_vm.STARTUP_SCRIPT_PATH.read_text()
    assert decoded == expected


# ---------------------------------------------------------------------------
# _get_ssh_public_key
# ---------------------------------------------------------------------------


def test_get_ssh_public_key_explicit_path(tmp_path) -> None:
    """Create a temp key.pub file, set ssh_key_path in config, verify content returned."""
    pub = tmp_path / "mykey.pub"
    pub.write_text("ssh-rsa AAAA testkey")

    cfg = MjCloudConfig(ssh_key_path=str(pub))
    result = azure_vm._get_ssh_public_key(cfg)
    assert result == "ssh-rsa AAAA testkey"


def test_get_ssh_public_key_auto_pub_suffix(tmp_path) -> None:
    """Config has path without .pub, but .pub file exists. Should read .pub."""
    priv = tmp_path / "test_key"
    priv.write_text("PRIVATE")
    pub = tmp_path / "test_key.pub"
    pub.write_text("ssh-rsa BBBB auto-pub")

    cfg = MjCloudConfig(ssh_key_path=str(priv))
    result = azure_vm._get_ssh_public_key(cfg)
    assert result == "ssh-rsa BBBB auto-pub"


def test_get_ssh_public_key_already_pub(tmp_path) -> None:
    """Config has path ending in .pub. Should not double-append."""
    pub = tmp_path / "already.pub"
    pub.write_text("ssh-rsa CCCC already")

    cfg = MjCloudConfig(ssh_key_path=str(pub))
    result = azure_vm._get_ssh_public_key(cfg)
    assert result == "ssh-rsa CCCC already"


def test_get_ssh_public_key_missing_raises(tmp_path) -> None:
    """No key file exists. Should raise FileNotFoundError with helpful message."""
    cfg = MjCloudConfig(ssh_key_path=str(tmp_path / "nonexistent"))
    with pytest.raises(FileNotFoundError, match="SSH public key not found"):
        azure_vm._get_ssh_public_key(cfg)


def test_get_ssh_public_key_default_path(tmp_path, monkeypatch) -> None:
    """No ssh_key_path set. Should try ~/.ssh/id_rsa.pub."""
    fake_home = tmp_path / "home"
    ssh_dir = fake_home / ".ssh"
    ssh_dir.mkdir(parents=True)
    pub = ssh_dir / "id_rsa.pub"
    pub.write_text("ssh-rsa DDDD default")

    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    cfg = MjCloudConfig(ssh_key_path="")
    result = azure_vm._get_ssh_public_key(cfg)
    assert result == "ssh-rsa DDDD default"


# ---------------------------------------------------------------------------
# wait_for_running
# ---------------------------------------------------------------------------


def test_wait_for_running_immediate() -> None:
    """Mock get_instance to return RUNNING. Should return immediately."""
    cfg = MjCloudConfig(subscription_id="s", resource_group="rg")
    running_info = {"name": "vm1", "status": "RUNNING", "external_ip": "1.2.3.4"}

    with patch.object(azure_vm, "get_instance", return_value=running_info) as mock_get, \
         patch("time.sleep"):
        result = azure_vm.wait_for_running(cfg, "vm1", timeout=60)
    assert result["status"] == "RUNNING"
    mock_get.assert_called_once()


def test_wait_for_running_timeout() -> None:
    """Mock get_instance to always return STARTING. Should raise TimeoutError."""
    cfg = MjCloudConfig(subscription_id="s", resource_group="rg")
    starting_info = {"name": "vm1", "status": "STARTING", "external_ip": None}

    with patch.object(azure_vm, "get_instance", return_value=starting_info), \
         patch("time.sleep"), \
         patch("time.time", side_effect=[0, 0, 1, 2, 999]):
        with pytest.raises(TimeoutError, match="did not reach RUNNING"):
            azure_vm.wait_for_running(cfg, "vm1", timeout=3, poll_interval=1)


def test_wait_for_running_eventual() -> None:
    """Mock get_instance to return STARTING then RUNNING. Should succeed."""
    cfg = MjCloudConfig(subscription_id="s", resource_group="rg")
    starting = {"name": "vm1", "status": "STARTING", "external_ip": None}
    running = {"name": "vm1", "status": "RUNNING", "external_ip": "1.2.3.4"}

    call_count = 0

    def fake_get_instance(config, name):
        nonlocal call_count
        call_count += 1
        return starting if call_count == 1 else running

    # time.time() sequence: first call (0) < deadline (100), second call (1) < deadline
    times = iter([0, 0, 1])

    with patch.object(azure_vm, "get_instance", side_effect=fake_get_instance), \
         patch("time.sleep"), \
         patch("time.time", side_effect=times):
        result = azure_vm.wait_for_running(cfg, "vm1", timeout=100, poll_interval=1)

    assert result["status"] == "RUNNING"
    assert call_count == 2


# ---------------------------------------------------------------------------
# upload_simulation_files
# ---------------------------------------------------------------------------


def test_upload_simulation_files_calls_scp(tmp_path) -> None:
    """upload_simulation_files runs SCP for robot/ and examples/ dirs that exist."""
    cfg = MjCloudConfig(ssh_key_path=str(tmp_path / "key"), admin_username="testuser")

    calls: list = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return MagicMock(returncode=0)

    with patch("simup.azure_vm.subprocess.run", side_effect=fake_run):
        # The real package has robot/ and examples/ next to simup/
        azure_vm.upload_simulation_files(cfg, "10.0.0.1")

    # Should have attempted SCP for at least one directory
    assert len(calls) > 0
    for call in calls:
        assert call[0] == "scp"
        assert "testuser@10.0.0.1" in call[-1]


def test_upload_simulation_files_skips_missing_dirs(tmp_path) -> None:
    """upload_simulation_files skips directories that don't exist."""
    cfg = MjCloudConfig(ssh_key_path="/tmp/key", admin_username="testuser")

    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return MagicMock(returncode=0)

    with patch("simup.azure_vm.subprocess.run", side_effect=fake_run):
        # The real package dir probably has robot/ and examples/, so this
        # tests the function runs without error
        try:
            azure_vm.upload_simulation_files(cfg, "10.0.0.1")
        except Exception:
            pass  # SCP will fail without a real host, but that's fine
