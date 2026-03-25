"""Azure VM operations for simup."""
from __future__ import annotations

import base64
import subprocess
import time
import uuid
from pathlib import Path
from typing import TypedDict

from azure.identity import DefaultAzureCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.resource import ResourceManagementClient

from .config import (
    MjCloudConfig,
    INSTANCE_TAG,
    NSG_NAME,
    VNET_NAME,
    SUBNET_NAME,
    DEFAULT_IMAGE_PUBLISHER,
    DEFAULT_IMAGE_OFFER,
    DEFAULT_IMAGE_SKU,
    DEFAULT_IMAGE_VERSION,
    PRESETS,
)


class InstanceInfo(TypedDict):
    name: str
    location: str
    status: str
    external_ip: str | None
    vm_size: str
    creation_timestamp: str


STARTUP_SCRIPT_PATH: Path = Path(__file__).parent / "startup_script.sh"


def _get_credential() -> DefaultAzureCredential:
    """Get Azure credential."""
    return DefaultAzureCredential()


def _generate_instance_name() -> str:
    """Generate a unique instance name."""
    short_id = uuid.uuid4().hex[:8]
    return f"simup-{short_id}"


def get_startup_script() -> str:
    """Read the startup script and encode as base64 for cloud-init custom_data."""
    content = STARTUP_SCRIPT_PATH.read_text()
    return base64.b64encode(content.encode()).decode()


def ensure_resource_group(config: MjCloudConfig) -> None:
    """Ensure the resource group exists."""
    credential = _get_credential()
    client = ResourceManagementClient(credential, config.subscription_id)

    # Check if the RG already exists in a different location
    try:
        existing_rg = client.resource_groups.get(config.resource_group)
        if existing_rg.location.lower() != config.location.lower():
            print(
                f"Warning: Resource group '{config.resource_group}' already exists "
                f"in '{existing_rg.location}', not '{config.location}'. "
                f"Using existing location '{existing_rg.location}'."
            )
            config.location = existing_rg.location
    except Exception:
        pass  # RG does not exist yet, will be created

    client.resource_groups.create_or_update(  # type: ignore[call-overload]
        config.resource_group,
        {"location": config.location},
    )


def ensure_nsg(config: MjCloudConfig) -> str:
    """Ensure Network Security Group exists with Jupyter + SSH rules. Returns NSG ID."""
    credential = _get_credential()
    network_client = NetworkManagementClient(credential, config.subscription_id)

    nsg_name = f"{NSG_NAME}-{config.location}"

    try:
        nsg = network_client.network_security_groups.get(
            config.resource_group, nsg_name
        )
        return nsg.id  # type: ignore[return-value]
    except Exception:
        pass

    nsg_params = {
        "location": config.location,
        "security_rules": [
            {
                "name": "allow-ssh",
                "protocol": "Tcp",
                "source_port_range": "*",
                "destination_port_range": "22",
                "source_address_prefix": "*",
                "destination_address_prefix": "*",
                "access": "Allow",
                "priority": 100,
                "direction": "Inbound",
            },
            {
                "name": "allow-jupyter",
                "protocol": "Tcp",
                "source_port_range": "*",
                "destination_port_range": "8888",
                "source_address_prefix": "*",
                "destination_address_prefix": "*",
                "access": "Allow",
                "priority": 110,
                "direction": "Inbound",
            },
        ],
        "tags": {INSTANCE_TAG: "true"},
    }

    poller = network_client.network_security_groups.begin_create_or_update(  # type: ignore[call-overload]
        config.resource_group, nsg_name, nsg_params
    )
    nsg = poller.result()
    return nsg.id  # type: ignore[return-value]


def ensure_network(config: MjCloudConfig) -> str:
    """Ensure VNet and subnet exist. Returns subnet ID."""
    credential = _get_credential()
    network_client = NetworkManagementClient(credential, config.subscription_id)

    vnet_name = f"{VNET_NAME}-{config.location}"
    subnet_name = SUBNET_NAME

    # Create or update VNet with subnet
    vnet_params = {
        "location": config.location,
        "address_space": {"address_prefixes": ["10.0.0.0/16"]},
        "subnets": [
            {
                "name": subnet_name,
                "address_prefix": "10.0.0.0/24",
            }
        ],
        "tags": {INSTANCE_TAG: "true"},
    }

    poller = network_client.virtual_networks.begin_create_or_update(  # type: ignore[call-overload]
        config.resource_group, vnet_name, vnet_params
    )
    poller.result()

    subnet = network_client.subnets.get(
        config.resource_group, vnet_name, subnet_name
    )
    return subnet.id  # type: ignore[return-value]


def _create_public_ip(config: MjCloudConfig, name: str) -> str:
    """Create a public IP address. Returns public IP ID."""
    credential = _get_credential()
    network_client = NetworkManagementClient(credential, config.subscription_id)

    ip_name = f"{name}-ip"
    poller = network_client.public_ip_addresses.begin_create_or_update(  # type: ignore[call-overload]
        config.resource_group,
        ip_name,
        {
            "location": config.location,
            "sku": {"name": "Standard"},
            "public_ip_allocation_method": "Static",
            "tags": {INSTANCE_TAG: "true"},
        },
    )
    ip = poller.result()
    return ip.id  # type: ignore[return-value]


def _create_nic(config: MjCloudConfig, name: str, subnet_id: str, nsg_id: str, public_ip_id: str) -> str:
    """Create a network interface. Returns NIC ID."""
    credential = _get_credential()
    network_client = NetworkManagementClient(credential, config.subscription_id)

    nic_name = f"{name}-nic"
    poller = network_client.network_interfaces.begin_create_or_update(  # type: ignore[call-overload]
        config.resource_group,
        nic_name,
        {
            "location": config.location,
            "ip_configurations": [
                {
                    "name": "default",
                    "subnet": {"id": subnet_id},
                    "public_ip_address": {"id": public_ip_id},
                }
            ],
            "network_security_group": {"id": nsg_id},
            "tags": {INSTANCE_TAG: "true"},
        },
    )
    nic = poller.result()
    return nic.id  # type: ignore[return-value]


def _get_ssh_public_key(config: MjCloudConfig) -> str:
    """Read SSH public key for VM authentication."""
    if config.ssh_key_path:
        key_path = Path(config.ssh_key_path)
        pub_key_path = key_path.with_suffix(".pub") if not key_path.suffix == ".pub" else key_path
    else:
        pub_key_path = Path.home() / ".ssh" / "id_rsa.pub"

    if not pub_key_path.exists():
        raise FileNotFoundError(
            f"SSH public key not found at {pub_key_path}. "
            "Generate one with: ssh-keygen -t rsa -b 4096\n"
            "Or set ssh_key_path in config: simup config --ssh-key ~/.ssh/mykey"
        )

    return pub_key_path.read_text().strip()


def upload_simulation_files(config: MjCloudConfig, ip: str) -> None:
    """Upload robot/ and examples/ directories to the VM via SCP."""
    package_dir = Path(__file__).parent.parent
    ssh_key_path = config.ssh_key_path or str(Path.home() / ".ssh" / "id_rsa")
    user = config.admin_username

    ssh_opts = [
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-i", ssh_key_path,
    ]

    for dirname in ("robot", "examples"):
        local_path = package_dir / dirname
        if not local_path.exists():
            continue
        cmd = [
            "scp", "-r", *ssh_opts,
            str(local_path),
            f"{user}@{ip}:~/{dirname}",
        ]
        print(f"Uploading {dirname}/ to {ip}...")
        subprocess.run(cmd, check=True, capture_output=True)


def create_instance(
    config: MjCloudConfig,
    name: str | None = None,
    preset: str | None = None,
    vm_size: str | None = None,
    disk_size_gb: int | None = None,
) -> InstanceInfo:
    """Create a new Azure VM with MuJoCo pre-installed.

    Returns dict with instance details (name, location, external_ip, status).
    """
    instance_name = name or _generate_instance_name()
    size = vm_size or (PRESETS[preset]["vm_size"] if preset else config.vm_size)
    disk = disk_size_gb or config.disk_size_gb

    credential = _get_credential()

    # Ensure infrastructure
    ensure_resource_group(config)
    nsg_id = ensure_nsg(config)
    subnet_id = ensure_network(config)

    # Create networking resources
    public_ip_id = _create_public_ip(config, instance_name)
    nic_id = _create_nic(config, instance_name, subnet_id, nsg_id, public_ip_id)

    # Get SSH key
    ssh_public_key = _get_ssh_public_key(config)

    # Create VM
    compute_client = ComputeManagementClient(credential, config.subscription_id)

    vm_params = {
        "location": config.location,
        "tags": {INSTANCE_TAG: "true"},
        "hardware_profile": {"vm_size": size},
        "storage_profile": {
            "image_reference": {
                "publisher": DEFAULT_IMAGE_PUBLISHER,
                "offer": DEFAULT_IMAGE_OFFER,
                "sku": DEFAULT_IMAGE_SKU,
                "version": DEFAULT_IMAGE_VERSION,
            },
            "os_disk": {
                "create_option": "FromImage",
                "managed_disk": {"storage_account_type": "Premium_LRS"},
                "disk_size_gb": disk,
                "delete_option": "Delete",
            },
        },
        "os_profile": {
            "computer_name": instance_name,
            "admin_username": config.admin_username,
            "linux_configuration": {
                "disable_password_authentication": True,
                "ssh": {
                    "public_keys": [
                        {
                            "path": f"/home/{config.admin_username}/.ssh/authorized_keys",
                            "key_data": ssh_public_key,
                        }
                    ]
                },
            },
            "custom_data": get_startup_script(),
        },
        "network_profile": {
            "network_interfaces": [
                {"id": nic_id, "properties": {"delete_option": "Delete"}},
            ]
        },
    }

    poller = compute_client.virtual_machines.begin_create_or_update(  # type: ignore[call-overload]
        config.resource_group, instance_name, vm_params
    )
    poller.result()

    info = wait_for_running(config, instance_name)

    # Upload simulation files to the VM
    if info["external_ip"]:
        try:
            upload_simulation_files(config, info["external_ip"])
        except Exception as e:
            print(f"Warning: Failed to upload simulation files: {e}")

    return info


def get_instance(config: MjCloudConfig, name: str) -> InstanceInfo:
    """Get VM details."""
    credential = _get_credential()
    compute_client = ComputeManagementClient(credential, config.subscription_id)
    network_client = NetworkManagementClient(credential, config.subscription_id)

    vm = compute_client.virtual_machines.get(
        config.resource_group, name, expand="instanceView"
    )

    # Get power state
    status = "Unknown"
    if vm.instance_view and vm.instance_view.statuses:
        for s in vm.instance_view.statuses:
            if s.code and s.code.startswith("PowerState/"):
                status = s.code.split("/")[1].upper()
                break

    # Get public IP
    external_ip: str | None = None
    if vm.network_profile and vm.network_profile.network_interfaces:
        nic_ref = vm.network_profile.network_interfaces[0]
        if nic_ref.id:
            nic_name = nic_ref.id.split("/")[-1]
            nic = network_client.network_interfaces.get(config.resource_group, nic_name)
            if nic.ip_configurations:
                for ip_config in nic.ip_configurations:
                    if ip_config.public_ip_address and ip_config.public_ip_address.id:
                        pip_name = ip_config.public_ip_address.id.split("/")[-1]
                        pip = network_client.public_ip_addresses.get(
                            config.resource_group, pip_name
                        )
                        external_ip = pip.ip_address
                        break

    return InstanceInfo(
        name=vm.name or "",
        location=vm.location or "",
        status=status,
        external_ip=external_ip,
        vm_size=(vm.hardware_profile.vm_size or "") if vm.hardware_profile else "",
        creation_timestamp=vm.tags.get("created", "") if vm.tags else "",
    )


def list_instances(config: MjCloudConfig) -> list[InstanceInfo]:
    """List all simup VMs."""
    credential = _get_credential()
    compute_client = ComputeManagementClient(credential, config.subscription_id)
    network_client = NetworkManagementClient(credential, config.subscription_id)

    instances: list[InstanceInfo] = []
    for vm in compute_client.virtual_machines.list(config.resource_group):
        has_tag = vm.tags and vm.tags.get(INSTANCE_TAG) == "true"
        if not has_tag:
            continue

        # Get power state
        status = "Unknown"
        vm_detail = compute_client.virtual_machines.get(
            config.resource_group, vm.name or "", expand="instanceView"
        )
        if vm_detail.instance_view and vm_detail.instance_view.statuses:
            for s in vm_detail.instance_view.statuses:
                if s.code and s.code.startswith("PowerState/"):
                    status = s.code.split("/")[1].upper()
                    break

        # Get public IP
        external_ip: str | None = None
        if vm.network_profile and vm.network_profile.network_interfaces:
            nic_ref = vm.network_profile.network_interfaces[0]
            if nic_ref.id:
                nic_name = nic_ref.id.split("/")[-1]
                try:
                    nic = network_client.network_interfaces.get(config.resource_group, nic_name)
                    if nic.ip_configurations:
                        for ip_config in nic.ip_configurations:
                            if ip_config.public_ip_address and ip_config.public_ip_address.id:
                                pip_name = ip_config.public_ip_address.id.split("/")[-1]
                                pip = network_client.public_ip_addresses.get(
                                    config.resource_group, pip_name
                                )
                                external_ip = pip.ip_address
                                break
                except Exception:
                    pass

        instances.append(InstanceInfo(
            name=vm.name or "",
            location=vm.location or "",
            status=status,
            external_ip=external_ip,
            vm_size=(vm.hardware_profile.vm_size or "") if vm.hardware_profile else "",
            creation_timestamp=vm.tags.get("created", "") if vm.tags else "",
        ))

    return instances


def delete_instance(config: MjCloudConfig, name: str) -> None:
    """Delete a VM and its associated resources (NIC, public IP, disk)."""
    credential = _get_credential()
    compute_client = ComputeManagementClient(credential, config.subscription_id)
    network_client = NetworkManagementClient(credential, config.subscription_id)

    # Delete VM (disk and NIC set to delete_option=Delete, so they go with it)
    poller = compute_client.virtual_machines.begin_delete(
        config.resource_group, name
    )
    poller.result()

    # Clean up public IP
    ip_name = f"{name}-ip"
    try:
        poller = network_client.public_ip_addresses.begin_delete(
            config.resource_group, ip_name
        )
        poller.result()
    except Exception:
        pass  # May already be deleted


def wait_for_running(
    config: MjCloudConfig, name: str, timeout: int = 300, poll_interval: int = 10
) -> InstanceInfo:
    """Wait for VM to reach RUNNING state."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = get_instance(config, name)
        if info["status"] == "RUNNING":
            return info
        time.sleep(poll_interval)
    raise TimeoutError(f"VM {name} did not reach RUNNING state within {timeout}s")


def deallocate_instance(config: MjCloudConfig, name: str) -> None:
    """Deallocate a VM (stop billing) without deleting it."""
    credential = _get_credential()
    compute_client = ComputeManagementClient(credential, config.subscription_id)
    poller = compute_client.virtual_machines.begin_deallocate(
        config.resource_group, name
    )
    poller.result()
