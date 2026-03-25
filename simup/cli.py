"""simup — my sim is up. Deploy MuJoCo simulation environments on Azure."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import webbrowser

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from .config import MjCloudConfig, PRESETS, CONFIG_DIR, PresetInfo
from . import azure_vm

console: Console = Console()

# Map preset VM sizes to the Azure quota family names used in `az vm list-usage`.
_VM_SIZE_TO_QUOTA_FAMILY: dict[str, str] = {
    "Standard_NC4as_T4_v3":      "Standard NCASv3_T4 Family",
    "Standard_NC8as_T4_v3":      "Standard NCASv3_T4 Family",
    "Standard_NC16as_T4_v3":     "Standard NCASv3_T4 Family",
    "Standard_NC6s_v3":          "Standard NCSv3 Family",
    "Standard_NC24ads_A100_v4":  "Standard NCADS_A100_v4 Family",
    "Standard_NC40ads_H100_v5":  "Standard NCADS_H100_v5 Family",
    "Standard_D4s_v3":           "Standard DSv3 Family",
}


def get_config(
    subscription: str | None = None,
    resource_group: str | None = None,
    location: str | None = None,
) -> MjCloudConfig:
    """Load config with CLI overrides."""
    config = MjCloudConfig.load()
    if subscription:
        config.subscription_id = subscription
    if resource_group:
        config.resource_group = resource_group
    if location:
        config.location = location

    if not config.subscription_id:
        console.print(
            "[red]No Azure subscription configured.[/red]\n"
            "Set it with: [bold]simup config --subscription YOUR_SUBSCRIPTION_ID[/bold]\n"
            "Or set AZURE_SUBSCRIPTION_ID or SIMUP_SUBSCRIPTION env var."
        )
        raise SystemExit(1)

    if not config.resource_group:
        console.print(
            "[red]No Azure resource group configured.[/red]\n"
            "Set it with: [bold]simup config --resource-group YOUR_RG[/bold]"
        )
        raise SystemExit(1)

    return config


def _ssh_command(config: MjCloudConfig, ip: str, remote_cmd: str, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    """Run a command on the VM via SSH and return the CompletedProcess."""
    ssh_args = [
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
        "-o", "BatchMode=yes",
    ]
    if config.ssh_key_path:
        ssh_args.extend(["-i", config.ssh_key_path])
    ssh_args.append(f"{config.admin_username}@{ip}")
    ssh_args.append(remote_cmd)
    return subprocess.run(ssh_args, capture_output=True, text=True, timeout=timeout)


@click.group()
@click.version_option(package_name="simup")
def cli() -> None:
    """simup — my sim is up.

    Deploy MuJoCo GPU simulation environments on Azure in one command.
    """
    pass


@cli.command()
@click.option("--subscription", help="Azure subscription ID")
@click.option("--resource-group", help="Azure resource group")
@click.option("--location", default=None, help="Azure location (default: westus2)")
@click.option("--name", default=None, help="VM name (auto-generated if not set)")
@click.option(
    "--preset",
    type=click.Choice(list(PRESETS.keys())),
    default="medium",
    help="VM size preset",
)
@click.option("--disk-size", type=int, default=None, help="OS disk size in GB")
@click.option("--dry-run", is_flag=True, help="Show what would be created without doing it")
@click.option("--max-hours", type=float, default=None, help="Auto-shutdown after this many hours (saves cost)")
def deploy(subscription: str | None, resource_group: str | None, location: str | None, name: str | None, preset: str, disk_size: int | None, dry_run: bool, max_hours: float | None) -> None:
    """Deploy a new MuJoCo simulation environment."""
    config = get_config(subscription, resource_group, location)
    preset_info = PRESETS[preset]

    if dry_run:
        auto_shutdown_line = f"\n[bold]Auto-shutdown:[/bold] after {max_hours}h" if max_hours else ""
        console.print(Panel(
            f"[bold]VM:[/bold] {name or 'simup-<auto>'}\n"
            f"[bold]Preset:[/bold] {preset} ({preset_info['description']})\n"
            f"[bold]Size:[/bold] {preset_info['vm_size']}\n"
            f"[bold]GPU:[/bold] {preset_info['gpu_type']}\n"
            f"[bold]Location:[/bold] {config.location}\n"
            f"[bold]Disk:[/bold] {disk_size or config.disk_size_gb}GB SSD\n"
            f"[bold]Subscription:[/bold] {config.subscription_id}\n"
            f"[bold]Resource Group:[/bold] {config.resource_group}"
            + auto_shutdown_line,
            title="[bold green]Dry Run - Would Create",
            box=box.ROUNDED,
        ))
        return

    # For GPU presets, check quota and suggest a better region if needed.
    if preset_info["gpu_count"] > 0 and not location:
        with console.status(f"[bold green]Checking GPU quota in {config.location}..."):
            alt_region = _find_best_region(config, preset, preset_info)

        if alt_region is not None:
            gpu_label = preset_info["gpu_type"]
            console.print(
                f"[yellow]No quota for {gpu_label} in {config.location}. "
                f"Found available quota in {alt_region}.[/yellow]"
            )
            if click.confirm("Deploy there instead?", default=False):
                config.location = alt_region
                console.print(f"[green]Using region: {alt_region}[/green]")

    console.print(f"[bold]Deploying MuJoCo environment[/bold] ({preset}: {preset_info['description']})")

    with console.status("[bold green]Creating VM..."):
        info = azure_vm.create_instance(
            config,
            name=name,
            preset=preset,
            disk_size_gb=disk_size,
        )

    console.print(f"[green]VM created:[/green] {info['name']}")

    with console.status("[bold green]Waiting for VM to start..."):
        info = azure_vm.wait_for_running(config, info["name"])

    # Schedule auto-shutdown if --max-hours was provided
    if max_hours:
        _schedule_auto_shutdown(config, info["name"], max_hours)

    ip = info["external_ip"]
    auto_shutdown_note = f"\n[bold]Auto-shutdown:[/bold] in {max_hours}h" if max_hours else ""
    console.print()
    console.print(Panel(
        f"[bold green]Your MuJoCo environment is running![/bold green]\n\n"
        f"[bold]VM:[/bold]         {info['name']}\n"
        f"[bold]IP:[/bold]         {ip}\n"
        f"[bold]Size:[/bold]       {info['vm_size']}\n"
        f"[bold]Status:[/bold]     {info['status']}"
        + auto_shutdown_note + "\n\n"
        f"[bold]SSH:[/bold]        [cyan]simup ssh {info['name']}[/cyan]\n"
        f"[bold]Jupyter:[/bold]    [cyan]http://{ip}:8888[/cyan]\n\n"
        f"[dim]Note: MuJoCo setup takes ~5-10 min after boot. "
        f"Check progress with: ssh in and tail -f /var/log/simup-setup.log[/dim]",
        title="[bold]simup",
        box=box.DOUBLE,
    ))


def _check_region_quota(region: str, vm_size: str) -> int | None:
    """Check available vCPU quota for a VM size in a given Azure region.

    Returns the number of available cores, or None if the check fails.
    """
    family = _VM_SIZE_TO_QUOTA_FAMILY.get(vm_size)
    if not family:
        return None

    try:
        result = subprocess.run(
            ["az", "vm", "list-usage", "--location", region, "--output", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None

        usages = json.loads(result.stdout)
        for u in usages:
            if u.get("name", {}).get("value", "") == family:
                limit = u.get("limit", 0)
                current = u.get("currentValue", 0)
                return int(max(limit - current, 0))
    except Exception:
        return None

    return 0


_CANDIDATE_REGIONS: list[str] = [
    "eastus", "eastus2", "westus2", "westus3",
    "centralus", "northcentralus", "southcentralus",
    "westeurope", "northeurope",
]


def _find_best_region(
    config: MjCloudConfig,
    preset_name: str,
    preset_info: PresetInfo,
) -> str | None:
    """Find a region with available GPU quota for the requested preset.

    Checks the configured region first. If it has quota, returns None (no
    switch needed). Otherwise scans common Azure regions and returns the
    first one that has available quota, or None if none found.
    """
    vm_size = preset_info["vm_size"]

    # Check configured region first.
    available = _check_region_quota(config.location, vm_size)
    if available is not None and available > 0:
        return None  # configured region is fine

    # Scan other regions.
    for region in _CANDIDATE_REGIONS:
        if region == config.location:
            continue
        avail = _check_region_quota(region, vm_size)
        if avail is not None and avail > 0:
            return region

    return None


def _schedule_auto_shutdown(config: MjCloudConfig, vm_name: str, hours: float) -> None:
    """Schedule auto-shutdown for a VM using Azure auto-shutdown."""
    from datetime import datetime, timedelta, timezone

    shutdown_time = datetime.now(timezone.utc) + timedelta(hours=hours)
    # Azure auto-shutdown uses HHmm format in the VM's timezone; we use UTC.
    shutdown_hhmm = shutdown_time.strftime("%H%M")

    try:
        subprocess.run(
            [
                "az", "vm", "auto-shutdown",
                "--resource-group", config.resource_group,
                "--name", vm_name,
                "--time", shutdown_hhmm,
            ],
            capture_output=True, text=True, timeout=30, check=True,
        )
        console.print(f"[green]Auto-shutdown scheduled at {shutdown_time.strftime('%Y-%m-%d %H:%M UTC')}[/green]")
    except Exception as exc:
        console.print(f"[yellow]WARN[/yellow] Could not schedule auto-shutdown: {exc}")


@cli.command("list")
@click.option("--subscription", help="Azure subscription ID")
@click.option("--resource-group", help="Azure resource group")
@click.option("--location", default=None, help="Azure location")
def list_instances(subscription: str | None, resource_group: str | None, location: str | None) -> None:
    """List all simup VMs."""
    config = get_config(subscription, resource_group, location)

    with console.status("[bold green]Fetching VMs..."):
        instances = azure_vm.list_instances(config)

    if not instances:
        console.print("[dim]No simup VMs found.[/dim]")
        return

    table = Table(title="simup VMs (Azure)", box=box.ROUNDED)
    table.add_column("Name", style="cyan")
    table.add_column("Status")
    table.add_column("IP")
    table.add_column("VM Size")
    table.add_column("Jupyter")

    for inst in instances:
        status_style = "green" if inst["status"] == "RUNNING" else "yellow"
        ip = inst["external_ip"] or "-"
        jupyter = f"http://{ip}:8888" if inst["external_ip"] else "-"
        table.add_row(
            inst["name"],
            f"[{status_style}]{inst['status']}[/{status_style}]",
            ip,
            inst["vm_size"],
            jupyter if inst["status"] == "RUNNING" else "-",
        )

    console.print(table)


@cli.command()
@click.argument("name")
@click.option("--subscription", help="Azure subscription ID")
@click.option("--resource-group", help="Azure resource group")
@click.option("--location", default=None, help="Azure location")
def status(name: str, subscription: str | None, resource_group: str | None, location: str | None) -> None:
    """Show details for a specific VM."""
    config = get_config(subscription, resource_group, location)

    try:
        info = azure_vm.get_instance(config, name)
    except Exception:
        console.print(f"[red]VM not found: {name}[/red]")
        raise SystemExit(1)

    ip = info["external_ip"] or "N/A"

    # Check setup progress via SSH if the VM is running and reachable
    setup_status = ""
    if info["status"] == "RUNNING" and info["external_ip"]:
        try:
            result = _ssh_command(
                config, info["external_ip"],
                "test -f /opt/simup/.setup-complete && echo READY || echo SETTING_UP",
            )
            if result.returncode == 0:
                output = result.stdout.strip()
                if output == "READY":
                    setup_status = "[green]Ready[/green]"
                else:
                    setup_status = "[yellow]Setting up...[/yellow]"
            else:
                setup_status = "[dim]Unknown (SSH failed)[/dim]"
        except Exception:
            setup_status = "[dim]Unknown (SSH unreachable)[/dim]"

    setup_line = f"\n[bold]Setup:[/bold]    {setup_status}" if setup_status else ""
    jupyter_line = f"\n[bold]Jupyter:[/bold]   http://{ip}:8888" if info["external_ip"] else ""

    console.print(Panel(
        f"[bold]Name:[/bold]      {info['name']}\n"
        f"[bold]Status:[/bold]    {info['status']}\n"
        f"[bold]IP:[/bold]        {ip}\n"
        f"[bold]Size:[/bold]      {info['vm_size']}\n"
        f"[bold]Location:[/bold]  {info['location']}"
        + setup_line
        + jupyter_line,
        title=f"[bold]{info['name']}",
        box=box.ROUNDED,
    ))


@cli.command()
@click.argument("name")
@click.option("--subscription", help="Azure subscription ID")
@click.option("--resource-group", help="Azure resource group")
@click.option("--location", default=None, help="Azure location")
def ssh(name: str, subscription: str | None, resource_group: str | None, location: str | None) -> None:
    """SSH into a simup VM."""
    config = get_config(subscription, resource_group, location)
    info = azure_vm.get_instance(config, name)

    if not info["external_ip"]:
        console.print("[red]VM has no public IP.[/red]")
        raise SystemExit(1)

    ssh_args = ["ssh"]
    if config.ssh_key_path:
        ssh_args.extend(["-i", config.ssh_key_path])
    ssh_args.append(f"{config.admin_username}@{info['external_ip']}")

    console.print(f"[dim]$ {' '.join(ssh_args)}[/dim]")
    os.execvp("ssh", ssh_args)


@cli.command()
@click.argument("name")
@click.option("--subscription", help="Azure subscription ID")
@click.option("--resource-group", help="Azure resource group")
@click.option("--location", default=None, help="Azure location")
@click.option("--open", "open_browser", is_flag=True, help="Open in browser")
def jupyter(name: str, subscription: str | None, resource_group: str | None, location: str | None, open_browser: bool) -> None:
    """Get Jupyter URL for a simup VM."""
    config = get_config(subscription, resource_group, location)
    info = azure_vm.get_instance(config, name)

    if not info["external_ip"]:
        console.print("[red]VM has no public IP.[/red]")
        raise SystemExit(1)

    base_url = f"http://{info['external_ip']}:8888"

    # Try to fetch the Jupyter token from the VM
    token = None
    try:
        result = _ssh_command(config, info["external_ip"], "cat /opt/simup/.jupyter-token")
        if result.returncode == 0 and result.stdout.strip():
            token = result.stdout.strip()
    except Exception:
        pass

    if token:
        url = f"{base_url}/?token={token}"
    else:
        url = base_url
        console.print("[dim]Could not retrieve Jupyter token — setup may still be in progress.[/dim]")

    console.print(f"[bold]Jupyter Lab:[/bold] [cyan]{url}[/cyan]")

    if open_browser:
        webbrowser.open(url)


@cli.command()
@click.argument("name")
@click.option("--subscription", help="Azure subscription ID")
@click.option("--resource-group", help="Azure resource group")
@click.option("--location", default=None, help="Azure location")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def destroy(name: str, subscription: str | None, resource_group: str | None, location: str | None, yes: bool) -> None:
    """Destroy a simup VM."""
    config = get_config(subscription, resource_group, location)

    if not yes:
        click.confirm(
            f"Destroy VM '{name}'? This cannot be undone.",
            abort=True,
        )

    with console.status(f"[bold red]Destroying {name}..."):
        azure_vm.delete_instance(config, name)

    console.print(f"[green]VM '{name}' destroyed.[/green]")


@cli.command()
@click.argument("name")
@click.option("--subscription", help="Azure subscription ID")
@click.option("--resource-group", help="Azure resource group")
@click.option("--location", default=None, help="Azure location")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def stop(name: str, subscription: str | None, resource_group: str | None, location: str | None, yes: bool) -> None:
    """Stop (deallocate) a VM without destroying it.

    This stops compute billing but keeps the disk. Use 'deploy' or the Azure
    portal to restart later.
    """
    config = get_config(subscription, resource_group, location)

    if not yes:
        click.confirm(
            f"Stop and deallocate VM '{name}'? Compute billing will stop but disk is retained.",
            abort=True,
        )

    with console.status(f"[bold yellow]Deallocating {name}..."):
        try:
            subprocess.run(
                [
                    "az", "vm", "deallocate",
                    "--resource-group", config.resource_group,
                    "--name", name,
                    "--no-wait",
                ],
                capture_output=True, text=True, timeout=60, check=True,
            )
        except subprocess.CalledProcessError as exc:
            console.print(f"[red]Failed to deallocate VM: {exc.stderr}[/red]")
            raise SystemExit(1)

    console.print(f"[green]VM '{name}' is being deallocated.[/green]")
    console.print("[dim]Compute billing will stop once deallocation completes. Disk charges still apply.[/dim]")
    console.print(f"[dim]To restart: az vm start --resource-group {config.resource_group} --name {name}[/dim]")


@cli.command()
@click.option("--subscription", help="Set Azure subscription ID")
@click.option("--resource-group", help="Set Azure resource group")
@click.option("--location", help="Set default location")
@click.option("--ssh-key", help="Set SSH private key path")
def config(subscription: str | None, resource_group: str | None, location: str | None, ssh_key: str | None) -> None:
    """Configure simup defaults."""
    cfg = MjCloudConfig.load()

    if subscription:
        cfg.subscription_id = subscription
    if resource_group:
        cfg.resource_group = resource_group
    if location:
        cfg.location = location
    if ssh_key:
        cfg.ssh_key_path = ssh_key

    if subscription or resource_group or location or ssh_key:
        cfg.save()
        console.print("[green]Configuration saved.[/green]")

    console.print(f"[bold]Subscription:[/bold]    {cfg.subscription_id or '[dim]not set[/dim]'}")
    console.print(f"[bold]Resource Group:[/bold]  {cfg.resource_group or '[dim]not set[/dim]'}")
    console.print(f"[bold]Location:[/bold]        {cfg.location}")
    console.print(f"[bold]VM Size:[/bold]         {cfg.vm_size}")
    console.print(f"[bold]Disk:[/bold]            {cfg.disk_size_gb}GB")
    console.print(f"[bold]SSH Key:[/bold]         {cfg.ssh_key_path or '[dim]~/.ssh/id_rsa[/dim]'}")
    console.print(f"\n[dim]Config file: {CONFIG_DIR / 'config.yaml'}[/dim]")


@cli.command()
@click.option("--subscription", help="Azure subscription ID")
@click.option("--resource-group", help="Azure resource group")
@click.option("--location", default=None, help="Azure location")
def preflight(subscription: str | None, resource_group: str | None, location: str | None) -> None:
    """Check all prerequisites before deploying."""
    import shutil
    from pathlib import Path

    all_ok = True

    # 1. Python version
    py_ver = sys.version_info
    if py_ver >= (3, 10):
        console.print(f"[green]PASS[/green] Python {py_ver.major}.{py_ver.minor}")
    else:
        console.print(f"[red]FAIL[/red] Python {py_ver.major}.{py_ver.minor} — need 3.10+")
        all_ok = False

    # 2. Azure CLI installed
    if shutil.which("az"):
        console.print("[green]PASS[/green] Azure CLI installed")
    else:
        console.print("[red]FAIL[/red] Azure CLI not found — install from https://aka.ms/installazurecli")
        all_ok = False

    # 3. Azure login
    try:
        result = subprocess.run(
            ["az", "account", "show", "--output", "json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            console.print("[green]PASS[/green] Azure CLI logged in")
        else:
            console.print("[red]FAIL[/red] Not logged in — run: az login")
            all_ok = False
    except Exception:
        console.print("[red]FAIL[/red] Could not check Azure login")
        all_ok = False

    # 4. Config
    try:
        config = get_config(subscription, resource_group, location)
        console.print(f"[green]PASS[/green] Config loaded (subscription: {config.subscription_id[:8]}...)")
    except SystemExit:
        console.print("[red]FAIL[/red] Missing config — run: simup config --subscription YOUR_ID --resource-group YOUR_RG")
        all_ok = False
        config = None

    # 5. SSH key
    if config:
        key_path = config.ssh_key_path or str(Path.home() / ".ssh" / "id_rsa")
        pub_key = key_path + ".pub" if not key_path.endswith(".pub") else key_path
        if Path(pub_key).exists():
            console.print(f"[green]PASS[/green] SSH public key found: {pub_key}")
        else:
            # Check common alternatives
            alt_keys = [
                Path.home() / ".ssh" / "id_ed25519.pub",
                Path.home() / ".ssh" / "id_rsa.pub",
            ]
            found = next((k for k in alt_keys if k.exists()), None)
            if found:
                console.print(f"[yellow]WARN[/yellow] Key not at {pub_key}, but found {found}")
                console.print(f"       Run: simup config --ssh-key {str(found).replace('.pub', '')}")
            else:
                console.print("[red]FAIL[/red] No SSH public key found — run: ssh-keygen -t rsa -b 4096")
                all_ok = False

    # 6. GPU quota check — check ALL preset VM families
    if config:
        try:
            result = subprocess.run(
                ["az", "vm", "list-usage", "--location", config.location, "--output", "json"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                usages = json.loads(result.stdout)

                # Build a lookup: family name -> {limit, currentValue}
                usage_by_family: dict[str, dict[str, int]] = {}
                for u in usages:
                    family_name = u.get("name", {}).get("value", "")
                    usage_by_family[family_name] = {
                        "limit": u.get("limit", 0),
                        "current": u.get("currentValue", 0),
                    }

                # Deduplicate families across presets
                seen_families: set[str] = set()
                any_gpu_available = False
                gpu_presets_available: list[str] = []

                console.print(f"\n[bold]GPU/VM Quota in {config.location}:[/bold]")

                for preset_name, preset_info in PRESETS.items():
                    vm_size = preset_info["vm_size"]
                    family = _VM_SIZE_TO_QUOTA_FAMILY.get(vm_size)
                    if not family or family in seen_families:
                        continue
                    seen_families.add(family)

                    quota = usage_by_family.get(family)
                    is_gpu = preset_info["gpu_count"] > 0

                    if quota and quota["limit"] > 0:
                        available = quota["limit"] - quota["current"]
                        console.print(
                            f"  [green]PASS[/green] {family}: "
                            f"{available}/{quota['limit']} cores available"
                        )
                        if is_gpu:
                            any_gpu_available = True
                            gpu_presets_available.append(preset_name)
                    else:
                        status_icon = "[red]FAIL[/red]" if is_gpu else "[dim]----[/dim]"
                        console.print(f"  {status_icon} {family}: no quota")

                if not any_gpu_available:
                    console.print(
                        "\n[red]FAIL[/red] No GPU quota available in this region."
                    )
                    console.print(
                        "       Request quota via Azure Portal > Subscriptions > Usage + quotas"
                    )
                    console.print(
                        "       Or deploy with [bold]--preset cpu[/bold] for CPU-only (~$0.19/hr)"
                    )
                    all_ok = False
                else:
                    console.print(
                        f"\n  Available GPU presets: [cyan]{', '.join(gpu_presets_available)}[/cyan]"
                    )
        except Exception:
            console.print("[yellow]WARN[/yellow] Could not check GPU quota")

    console.print()
    if all_ok:
        console.print("[bold green]All checks passed! Ready to deploy.[/bold green]")
    else:
        console.print("[bold red]Some checks failed. Fix the issues above before deploying.[/bold red]")


@cli.command()
@click.argument("name")
@click.argument("remote_path")
@click.argument("local_path", default=".")
@click.option("--subscription", help="Azure subscription ID")
@click.option("--resource-group", help="Azure resource group")
@click.option("--location", default=None, help="Azure location")
def download(name: str, remote_path: str, local_path: str, subscription: str | None, resource_group: str | None, location: str | None) -> None:
    """Download a file from a simup VM.

    Example: simup download myvm /tmp/humanoid_sim.mp4 ./
    """
    config = get_config(subscription, resource_group, location)
    info = azure_vm.get_instance(config, name)

    if not info["external_ip"]:
        console.print("[red]VM has no public IP.[/red]")
        raise SystemExit(1)

    scp_args = ["scp"]
    if config.ssh_key_path:
        scp_args.extend(["-i", config.ssh_key_path])
    scp_args.append(f"{config.admin_username}@{info['external_ip']}:{remote_path}")
    scp_args.append(local_path)

    console.print(f"[dim]$ {' '.join(scp_args)}[/dim]")
    result = subprocess.run(scp_args)
    if result.returncode == 0:
        console.print(f"[green]Downloaded to {local_path}[/green]")
    else:
        console.print("[red]Download failed.[/red]")
        raise SystemExit(1)


@cli.command()
def presets() -> None:
    """Show available VM size presets."""
    table = Table(title="VM Size Presets (Azure)", box=box.ROUNDED)
    table.add_column("Preset", style="cyan bold")
    table.add_column("VM Size")
    table.add_column("GPU")
    table.add_column("Description")

    for name, info in PRESETS.items():
        table.add_row(
            name,
            info["vm_size"],
            info["gpu_type"],
            info["description"],
        )

    console.print(table)


if __name__ == "__main__":
    cli()
