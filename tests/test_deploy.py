"""End-to-end test: deploy a CPU VM, run a simulation, download the video.

Marked ``@pytest.mark.slow`` so it is skipped in normal CI runs.
Run with:  pytest -m slow --timeout=1800 tests/test_deploy.py
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Timeouts (seconds)
# ---------------------------------------------------------------------------
DEPLOY_TIMEOUT = 600       # 10 min
SETUP_POLL_TIMEOUT = 900   # 15 min
SETUP_POLL_INTERVAL = 30   # seconds between SSH polls
SIMULATE_TIMEOUT = 300     # 5 min
DOWNLOAD_TIMEOUT = 120     # 2 min
DESTROY_TIMEOUT = 300      # 5 min
LIST_TIMEOUT = 60          # 1 min


def _run(args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    """Run a subprocess, printing stdout/stderr on failure."""
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        print(f"STDOUT:\n{result.stdout}")
        print(f"STDERR:\n{result.stderr}")
    return result


def _extract_vm_name(output: str) -> str:
    """Parse the VM name from ``simup deploy`` output."""
    match = re.search(r"VM created:\s+(\S+)", output)
    if match:
        return match.group(1)
    # Fallback: look for the "VM:" line in the summary panel
    match = re.search(r"VM:\s+(\S+)", output)
    if match:
        return match.group(1)
    raise RuntimeError(f"Could not extract VM name from deploy output:\n{output}")


def _extract_ip(output: str) -> str:
    """Parse the external IP from ``simup deploy`` output."""
    match = re.search(r"IP:\s+(\d+\.\d+\.\d+\.\d+)", output)
    if match:
        return match.group(1)
    raise RuntimeError(f"Could not extract IP from deploy output:\n{output}")


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_full_deploy_simulate_download(deployed_vm: Any) -> None:
    """Deploy CPU VM -> wait for setup -> simulate -> download video -> destroy."""

    # ------------------------------------------------------------------
    # 1. Deploy a CPU VM
    # ------------------------------------------------------------------
    result = _run(["simup", "deploy", "--preset", "cpu"], timeout=DEPLOY_TIMEOUT)
    assert result.returncode == 0, f"simup deploy failed:\n{result.stderr}"

    combined_output = result.stdout + result.stderr
    vm_name = _extract_vm_name(combined_output)
    deployed_vm.name = vm_name          # register for cleanup fixture
    vm_ip = _extract_ip(combined_output)

    print(f"VM deployed: {vm_name} @ {vm_ip}")

    # ------------------------------------------------------------------
    # 2. Wait for setup to complete (poll /opt/simup/.setup-complete)
    # ------------------------------------------------------------------
    deadline = time.time() + SETUP_POLL_TIMEOUT
    setup_complete = False

    while time.time() < deadline:
        try:
            ssh_result = subprocess.run(
                [
                    "ssh",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=5",
                    "-o", "BatchMode=yes",
                    f"azureuser@{vm_ip}",
                    "test -f /opt/simup/.setup-complete && echo READY || echo WAITING",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if ssh_result.returncode == 0 and "READY" in ssh_result.stdout:
                setup_complete = True
                print("Setup complete on VM.")
                break
        except (subprocess.TimeoutExpired, Exception) as exc:
            print(f"SSH poll attempt failed ({exc}), retrying...")

        time.sleep(SETUP_POLL_INTERVAL)

    assert setup_complete, (
        f"VM setup did not complete within {SETUP_POLL_TIMEOUT}s. "
        "Check /var/log/simup-setup.log on the VM."
    )

    # ------------------------------------------------------------------
    # 3. Run the simulation on the VM
    # ------------------------------------------------------------------
    remote_output_path = "/tmp/humanoid_sim.mp4"
    sim_result = subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            "-o", "BatchMode=yes",
            f"azureuser@{vm_ip}",
            f"cd /opt/simup && python3 robot/simulate.py --duration 2 --video --output {remote_output_path}",
        ],
        capture_output=True,
        text=True,
        timeout=SIMULATE_TIMEOUT,
    )
    print(f"simulate stdout: {sim_result.stdout}")
    print(f"simulate stderr: {sim_result.stderr}")
    assert sim_result.returncode == 0, f"Simulation failed:\n{sim_result.stderr}"

    # ------------------------------------------------------------------
    # 4. Download the video via ``simup download``
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmpdir:
        local_video = os.path.join(tmpdir, "humanoid_sim.mp4")

        dl_result = _run(
            ["simup", "download", vm_name, remote_output_path, local_video],
            timeout=DOWNLOAD_TIMEOUT,
        )
        assert dl_result.returncode == 0, f"simup download failed:\n{dl_result.stderr}"

        # ----------------------------------------------------------
        # 5. Verify the video file exists and is non-empty
        # ----------------------------------------------------------
        assert os.path.isfile(local_video), f"Video file not found at {local_video}"
        file_size = os.path.getsize(local_video)
        assert file_size > 0, "Downloaded video is 0 bytes"
        print(f"Video downloaded: {local_video} ({file_size} bytes)")

    # ------------------------------------------------------------------
    # 6. Destroy the VM
    # ------------------------------------------------------------------
    destroy_result = _run(
        ["simup", "destroy", vm_name, "-y"],
        timeout=DESTROY_TIMEOUT,
    )
    assert destroy_result.returncode == 0, f"simup destroy failed:\n{destroy_result.stderr}"
    print(f"VM {vm_name} destroyed.")

    # Clear the cleanup handle so the fixture does not try to destroy again
    deployed_vm.name = None

    # ------------------------------------------------------------------
    # 7. Verify the VM is gone
    # ------------------------------------------------------------------
    list_result = _run(["simup", "list"], timeout=LIST_TIMEOUT)
    assert list_result.returncode == 0, f"simup list failed:\n{list_result.stderr}"
    # The destroyed VM name should no longer appear in the listing
    assert vm_name not in list_result.stdout, (
        f"VM {vm_name} still appears in 'simup list' after destroy"
    )
    print(f"Confirmed: {vm_name} is no longer listed.")
