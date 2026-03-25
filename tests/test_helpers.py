"""Unit tests for test helper functions in conftest and test_deploy."""
from __future__ import annotations

import pytest

from tests.conftest import _get_vm_name_from_deploy_output
from tests.test_deploy import _extract_vm_name, _extract_ip


# ---------------------------------------------------------------------------
# _extract_vm_name (from test_deploy)
# ---------------------------------------------------------------------------


def test_extract_vm_name_from_created_line() -> None:
    """Input 'VM created: simup-abc123' returns 'simup-abc123'."""
    result = _extract_vm_name("VM created: simup-abc123")
    assert result == "simup-abc123"


def test_extract_vm_name_from_panel() -> None:
    """Input 'VM: simup-abc123' returns 'simup-abc123'."""
    result = _extract_vm_name("VM: simup-abc123")
    assert result == "simup-abc123"


def test_extract_vm_name_no_match_raises() -> None:
    """Input 'no vm here' raises RuntimeError."""
    with pytest.raises(RuntimeError, match="Could not extract VM name"):
        _extract_vm_name("no vm here")


# ---------------------------------------------------------------------------
# _extract_ip (from test_deploy)
# ---------------------------------------------------------------------------


def test_extract_ip() -> None:
    """Input 'IP: 10.0.0.1' returns '10.0.0.1'."""
    result = _extract_ip("IP: 10.0.0.1")
    assert result == "10.0.0.1"


def test_extract_ip_no_match_raises() -> None:
    """Input 'no ip here' raises RuntimeError."""
    with pytest.raises(RuntimeError, match="Could not extract IP"):
        _extract_ip("no ip here")


# ---------------------------------------------------------------------------
# _get_vm_name_from_deploy_output (from conftest)
# ---------------------------------------------------------------------------


def test_get_vm_name_returns_none() -> None:
    """conftest helper returns None for bad input."""
    result = _get_vm_name_from_deploy_output("no vm here at all")
    assert result is None


def test_get_vm_name_from_created() -> None:
    """conftest helper finds name from 'VM created:' line."""
    result = _get_vm_name_from_deploy_output("VM created: simup-deadbeef")
    assert result == "simup-deadbeef"
