"""Shared fixtures for end-to-end tests."""
from __future__ import annotations

import subprocess
import re
from typing import Any

import pytest


def _get_vm_name_from_deploy_output(output: str) -> str | None:
    """Extract the VM name from ``simup deploy`` output.

    The CLI prints a line like:  VM created: simup-abc123
    """
    match = re.search(r"VM created:\s+(\S+)", output)
    if match:
        return match.group(1)
    # Fallback: look for "VM:" in the panel output
    match = re.search(r"VM:\s+(\S+)", output)
    return match.group(1) if match else None


@pytest.fixture()
def deployed_vm() -> Any:
    """Deploy a CPU VM and guarantee it is destroyed after the test.

    Yields the VM name so the test body can use it.  If the test fails
    (or is interrupted), the finaliser still runs ``simup destroy -y``.
    """
    class _Handle:
        """Simple mutable container so the test can set the VM name."""

        name: str | None = None

    handle = _Handle()
    yield handle

    # ---------- teardown ----------
    if handle.name:
        print(f"\n[cleanup] destroying VM {handle.name}")
        try:
            subprocess.run(
                ["simup", "destroy", handle.name, "-y"],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except Exception as exc:
            print(f"[cleanup] WARNING: destroy failed: {exc}")
