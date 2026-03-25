<p align="center">
  <img src="assets/simup_banner.png" alt="simup" width="600">
</p>

# simup: One-Click MuJoCo on Azure ML

`simup` ("my sim is up") is a simple CLI for deploying MuJoCo simulation environments on Azure ML. One command, and your sim is up.

![MuJoCo Humanoid on Azure ML](assets/humanoid_hero.png)

![Humanoid simulation demo](assets/humanoid_demo.gif)

---

## Why This Exists

MuJoCo is a beautiful physics engine with an elegant API. But We hit surprising issues deploying it on Azure ML. We doumented the painful journey in our blog post **[Deploying MuJoCo on Azure ML](https://www.hapticlabs.ai/blog/2026/03/21/deploying-mujoco-on-azure-ml)** and this `simup` is how we share a wrapper that helps people avoid these issues.

The goal is that `simup deploy` should be as easy as `pip install` for deploying MuJoCo on Azure ML.

---

## What You Get from `simup`

A **headless** VM in the cloud with everything pre-installed:
- MuJoCo physics engine
- dm_control humanoid demo (21 actuators, 17 bodies, 22 joints)
- Jupyter Lab in your browser for interactive simulation
- Example scripts for video rendering and benchmarking

**Important:** The VM has no monitor or GUI. (It's a cloud server, not a gaming PC.) You interact via:
1. **Jupyter Lab** (browser) -- best for interactive work and inline video
2. **SSH** -- run scripts, render video to MP4, download via `simup download`

---

## Prerequisites

Before you start, make sure you have:

1. **Python 3.10+** (check with `python3 --version`)
2. **Azure CLI** installed ([install guide](https://aka.ms/installazurecli))
3. **Azure account** with an active subscription
4. **SSH key** (`~/.ssh/id_rsa` by default, or specify with `--ssh-key`)

That's the whole list. No GPU quota needed if you start with `--preset cpu`.

### Finding your Subscription ID and Resource Group

`simup` needs two things from your Azure account: a **Subscription ID** and a **Resource Group**. If you're coming from AWS or GCP, here's the translation:

| Azure | AWS equivalent | GCP equivalent |
|-------|---------------|----------------|
| **Subscription** | AWS Account | GCP Project |
| **Resource Group** | No direct equivalent (think of it as a folder that groups related resources -- VMs, networks, disks -- so you can manage and delete them together) | GCP Resource Group / Labels |

**To find your Subscription ID:**
- Go to [portal.azure.com](https://portal.azure.com) > **Subscriptions** > copy the **Subscription ID**
- Or run: `az account show --query id -o tsv`

**To find or create a Resource Group:**
- Go to [portal.azure.com](https://portal.azure.com) > **Resource groups**
- Or create one: `az group create --name my-mujoco-rg --location eastus`

---

## Quick Start

Setup takes **5-10 minutes** after the VM starts. Monitor progress:

```bash
simup ssh <name>
tail -f /var/log/simup-setup.log
```

```bash
# 1. Authenticate with Azure
az login

# 2. Clone and install
git clone https://github.com/Haptic-AI/one-click-mujoco-azure.git
cd one-click-mujoco-azure
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 3. Configure (replace with your values)
simup config --subscription YOUR_SUBSCRIPTION_ID --resource-group YOUR_RG
simup config --ssh-key ~/.ssh/mykey   # optional, defaults to ~/.ssh/id_rsa

# 4. Check everything is ready
simup preflight

# 5. Deploy
simup deploy --preset cpu             # CPU (~$0.19/hr, no GPU quota needed)
simup deploy                          # medium T4 GPU (default, ~$0.75/hr, requires GPU quota)
simup deploy --dry-run                # preview without creating
```

---

## Viewing Your Simulation

The VM is headless -- there is no display window. Here's how to see your work:

### Option 1: Jupyter Lab (recommended)

```bash
simup jupyter <name> --open
```

Opens Jupyter Lab in your browser. The demo notebook renders video inline -- no file downloads needed. You'll see the humanoid. It'll be standing there. It's beautiful.

### Option 2: Render + Download

```bash
# SSH in and render a video
simup ssh <name>
python3 robot/simulate.py --video
# Video saved to /tmp/humanoid_sim.mp4 on the VM
exit

# Download it (this is why we built simup -- beats writing scp commands by hand)
simup download <name> /tmp/humanoid_sim.mp4 ./
open humanoid_sim.mp4
```

---

## The `simup` CLI

| Command | What it does |
|---------|-------------|
| `simup preflight` | Checks Python version, Azure login, SSH keys, GPU quota -- everything you need before deploying |
| `simup deploy` | Provisions a full VM (VNet, NSG, public IP, NIC, disk) and runs the MuJoCo setup script |
| `simup deploy --preset cpu` | Same as above but with a CPU-only VM -- no GPU quota required |
| `simup deploy --dry-run` | Shows what would be created without actually creating anything |
| `simup list` | Lists all `simup`-created VMs in your resource group |
| `simup status <name>` | Shows IP, size, region, and Jupyter URL for a specific VM |
| `simup ssh <name>` | Opens an SSH session to the VM (uses your configured key) |
| `simup jupyter <name>` | Prints the Jupyter Lab URL; add `--open` to launch it in your browser |
| `simup download <name> <remote> [local]` | Downloads files from the VM to your machine (no more Googling SCP syntax) |
| `simup destroy <name>` | Tears down the VM and all associated resources. **This is the one that stops billing. Use it.** |
| `simup config` | View or set defaults (subscription, resource group, location, SSH key) |
| `simup presets` | Shows all available VM size options with GPU type and cost |

---

## VM Presets

| Preset | GPU | VM Size | Est. Cost |
|--------|-----|---------|-----------|
| **`cpu`** | None (CPU only) | Standard_D4s_v3 | ~$0.19/hr |
| `small` | 1x T4 | Standard_NC4as_T4_v3 | ~$0.53/hr |
| `medium` (default) | 1x T4 | Standard_NC8as_T4_v3 | ~$0.75/hr |
| `large` | 1x T4 | Standard_NC16as_T4_v3 | ~$1.20/hr |
| `v100` | 1x V100 | Standard_NC6s_v3 | ~$3.06/hr |
| `a100` | 1x A100 80GB | Standard_NC24ads_A100_v4 | ~$3.67/hr |
| `h100` | 1x H100 NVL | Standard_NC40ads_H100_v5 | ~$8.00/hr |

VMs bill while running. Run `simup destroy <name>` when done.

Most users should start with `cpu`. MuJoCo works fine without a GPU -- it's just slower for large-scale simulations. GPU presets require [GPU quota](#requesting-gpu-quota) which new Azure subscriptions don't have by default.

### When to Use Which

| Use Case | Recommended | Why |
|----------|-------------|-----|
| Try MuJoCo for the first time | `cpu` | No quota needed, cheapest |
| Interactive dev, debugging | `cpu` or `small` | Good enough for most work |
| Video rendering, demos | `medium` or `large` | More CPU/GPU helps with encoding |
| Batched RL training (1000+ envs) | `a100` or `h100` | Need VRAM + MJX throughput |

---

## Project Structure

```
one-click-mujoco-azure/
├── pyproject.toml              # Package config, "simup" CLI entry point
├── simup/                      # The CLI package (this is what `pip install -e .` installs)
│   ├── cli.py                  # All user-facing commands (Click + Rich)
│   ├── config.py               # Config loading (~/.simup/), presets, env vars
│   ├── azure_vm.py             # Azure SDK calls: create/get/list/delete VMs
│   └── startup_script.sh       # Cloud-init script that runs on first VM boot
├── robot/                      # Simulation code (deployed to the VM)
│   ├── simulate.py             # Headless humanoid sim + video rendering
│   └── humanoid_demo.ipynb     # Interactive Jupyter demo
├── examples/                   # Benchmarks and demos (deployed to the VM)
│   ├── humanoid_walk.py        # CPU vs GPU benchmark
│   └── batched_humanoid_mjx.py # 1024 parallel envs with JAX
├── tests/                      # 70 unit tests (run locally, no Azure needed)
│   ├── test_config.py          # Config loading, env vars, save/load
│   ├── test_azure_vm.py        # Name generation, SSH keys, wait logic
│   ├── test_cli.py             # All CLI commands via CliRunner
│   └── test_helpers.py         # Output parsing helpers
└── .github/workflows/ci.yml   # CI: lint, test, type-check on every PR
```

**How the pieces connect:** `cli.py` is the entry point -- it parses commands, loads config from `config.py`, and calls `azure_vm.py` to provision infrastructure. When a VM boots, `startup_script.sh` installs MuJoCo, JAX, and Jupyter, then deploys the files from `robot/` and `examples/`.

---

## Learning Path

New to robotics simulation? Here's a suggested path after your first deploy:

1. **Open the Jupyter notebook** -- `simup jupyter <name> --open` and run `humanoid_demo.ipynb`
2. **Understand the model** -- The notebook shows joint counts, actuators, and observation spaces
3. **Try other dm_control tasks** -- Change `humanoid.stand` to `humanoid.walk` or `humanoid.run`
4. **Run the benchmark** -- `python3 examples/humanoid_walk.py` compares CPU vs GPU performance
5. **Scale up** -- `python3 examples/batched_humanoid_mjx.py` runs 1024 parallel environments via JAX
6. **Bring your own robot** -- Replace the model loading in `robot/simulate.py` with your own MJCF XML (convert from URDF if needed)

**Resources:**
- [MuJoCo Documentation](https://mujoco.readthedocs.io/)
- [dm_control Suite](https://github.com/google-deepmind/dm_control)
- [MuJoCo MJX (GPU)](https://mujoco.readthedocs.io/en/stable/mjx.html)
- [MuJoCo Playground](https://github.com/google-deepmind/mujoco_playground)

---

## Requesting GPU Quota

New Azure subscriptions have **0 GPU quota** by default. You must request it:

1. Go to [Azure Portal](https://portal.azure.com) > **Subscriptions** > your subscription
2. Click **Usage + quotas** in the left sidebar
3. Search for **Standard NCASv3_T4 Family** (cheapest GPU option)
4. Click the edit icon and request a **new limit of 8** (or more)
5. Submit -- approval can be instant or take up to a few business days

While waiting, use `simup deploy --preset cpu` to get started on a CPU VM. Physics doesn't care what hardware it runs on.

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `requires a different Python: 3.9.x not in '>=3.10'` | Install Python 3.10+: `brew install python@3.12` (macOS) or `apt install python3.12` (Ubuntu) |
| `OperationNotAllowed / exceeding quota` | Request GPU quota (see [above](#requesting-gpu-quota)), or use `--preset cpu` |
| `SSH public key not found` | Generate: `ssh-keygen -t rsa -b 4096`, or set path: `simup config --ssh-key ~/.ssh/mykey` |
| `InvalidResourceGroupLocation` | Your resource group is in a different region. Use `--location` matching your RG, or create a new RG |
| `OpenGL platform library has not been loaded` | Set `MUJOCO_GL=egl` (GPU) or `MUJOCO_GL=osmesa` (CPU) before running |
| `python: command not found` | Use `python3` instead of `python` |
| VM running but MuJoCo not installed yet | Setup takes 5-10 min after boot. Check: `tail -f /var/log/simup-setup.log` |

---

## License

MIT

---

Built by [Haptic AI](https://www.hapticlabs.ai). If this saved you a few hours of Azure debugging, we did our job.
