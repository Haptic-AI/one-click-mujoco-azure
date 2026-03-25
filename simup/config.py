"""simup — Configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import yaml


class PresetInfo(TypedDict):
    vm_size: str
    gpu_count: int
    gpu_type: str
    description: str


CONFIG_DIR: Path = Path.home() / ".simup"
CONFIG_FILE: Path = CONFIG_DIR / "config.yaml"

PRESETS: dict[str, PresetInfo] = {
    "small": {
        "vm_size": "Standard_NC4as_T4_v3",
        "gpu_count": 1,
        "gpu_type": "NVIDIA T4",
        "description": "1x T4, 4 vCPU, 28GB RAM (~$0.53/hr)",
    },
    "medium": {
        "vm_size": "Standard_NC8as_T4_v3",
        "gpu_count": 1,
        "gpu_type": "NVIDIA T4",
        "description": "1x T4, 8 vCPU, 56GB RAM (~$0.75/hr)",
    },
    "large": {
        "vm_size": "Standard_NC16as_T4_v3",
        "gpu_count": 1,
        "gpu_type": "NVIDIA T4",
        "description": "1x T4, 16 vCPU, 110GB RAM (~$1.20/hr)",
    },
    "v100": {
        "vm_size": "Standard_NC6s_v3",
        "gpu_count": 1,
        "gpu_type": "NVIDIA V100",
        "description": "1x V100, 6 vCPU, 112GB RAM (~$3.06/hr)",
    },
    "a100": {
        "vm_size": "Standard_NC24ads_A100_v4",
        "gpu_count": 1,
        "gpu_type": "NVIDIA A100 80GB",
        "description": "1x A100, 24 vCPU, 220GB RAM (~$3.67/hr)",
    },
    "h100": {
        "vm_size": "Standard_NC40ads_H100_v5",
        "gpu_count": 1,
        "gpu_type": "NVIDIA H100 NVL",
        "description": "1x H100, 40 vCPU, 320GB RAM (~$8.00/hr)",
    },
    "cpu": {
        "vm_size": "Standard_D4s_v3",
        "gpu_count": 0,
        "gpu_type": "None (CPU only)",
        "description": "CPU only, 4 vCPU, 16GB RAM (~$0.19/hr)",
    },
}

DEFAULT_LOCATION: str = "westus2"
DEFAULT_VM_SIZE: str = "Standard_NC8as_T4_v3"
DEFAULT_DISK_SIZE_GB: int = 100
DEFAULT_ADMIN_USERNAME: str = "azureuser"
DEFAULT_IMAGE_PUBLISHER: str = "Canonical"
DEFAULT_IMAGE_OFFER: str = "0001-com-ubuntu-server-jammy"
DEFAULT_IMAGE_SKU: str = "22_04-lts-gen2"
DEFAULT_IMAGE_VERSION: str = "latest"
INSTANCE_TAG: str = "simup"
NSG_NAME: str = "simup-nsg"
VNET_NAME: str = "simup-vnet"
SUBNET_NAME: str = "simup-subnet"


@dataclass
class MjCloudConfig:
    subscription_id: str = ""
    resource_group: str = ""
    location: str = DEFAULT_LOCATION
    vm_size: str = DEFAULT_VM_SIZE
    disk_size_gb: int = DEFAULT_DISK_SIZE_GB
    admin_username: str = DEFAULT_ADMIN_USERNAME
    ssh_key_path: str = ""

    @classmethod
    def load(cls) -> MjCloudConfig:
        """Load config from ~/.simup/config.yaml, env vars, or defaults."""
        config = cls()

        # Load from file
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                data = yaml.safe_load(f) or {}
            for key, value in data.items():
                if hasattr(config, key):
                    setattr(config, key, value)

        # Env var overrides
        if sub := os.environ.get("SIMUP_SUBSCRIPTION"):
            config.subscription_id = sub
        if rg := os.environ.get("SIMUP_RESOURCE_GROUP"):
            config.resource_group = rg
        if loc := os.environ.get("SIMUP_LOCATION"):
            config.location = loc
        if os.environ.get("AZURE_SUBSCRIPTION_ID"):
            config.subscription_id = config.subscription_id or os.environ["AZURE_SUBSCRIPTION_ID"]

        return config

    def save(self) -> None:
        """Save config to ~/.simup/config.yaml."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "subscription_id": self.subscription_id,
            "resource_group": self.resource_group,
            "location": self.location,
            "vm_size": self.vm_size,
            "disk_size_gb": self.disk_size_gb,
            "admin_username": self.admin_username,
            "ssh_key_path": self.ssh_key_path,
        }
        with open(CONFIG_FILE, "w") as f:
            yaml.dump(data, f, default_flow_style=False)
