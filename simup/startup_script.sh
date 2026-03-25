#!/bin/bash
# simup — VM Startup Script
# Installs NVIDIA drivers, MuJoCo, JAX, Jupyter on a fresh Ubuntu 22.04 VM
# Auto-deploys dm_control humanoid demo and starts simulation environment
set -euo pipefail

LOG_FILE="/var/log/simup-setup.log"
SETUP_MARKER="/opt/simup/.setup-complete"

exec > >(tee -a "$LOG_FILE") 2>&1
echo "=== simup setup started at $(date) ==="

# Skip if already set up
if [ -f "$SETUP_MARKER" ]; then
    echo "Setup already complete, starting services..."
    systemctl start jupyter
    exit 0
fi

mkdir -p /opt/simup

# System updates
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-pip python3-venv git wget curl tmux htop ffmpeg

# Detect GPU presence
HAS_GPU=false
if lspci | grep -qi nvidia; then
    HAS_GPU=true
    echo "=== NVIDIA GPU detected ==="
else
    echo "=== No NVIDIA GPU detected, using CPU-only mode ==="
fi

if [ "$HAS_GPU" = true ]; then
    # Install NVIDIA drivers + CUDA toolkit
    apt-get install -y linux-headers-$(uname -r)

    # Install NVIDIA driver
    apt-get install -y nvidia-driver-535 nvidia-utils-535

    # Install CUDA toolkit 12.2
    wget -q https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
    dpkg -i cuda-keyring_1.1-1_all.deb
    apt-get update -y
    apt-get install -y cuda-toolkit-12-2

    # Set up environment for GPU
    cat >> /etc/environment << 'ENVEOF'
MUJOCO_GL=egl
CUDA_HOME=/usr/local/cuda-12.2
PATH=/usr/local/cuda-12.2/bin:$PATH
LD_LIBRARY_PATH=/usr/local/cuda-12.2/lib64:$LD_LIBRARY_PATH
ENVEOF

    export MUJOCO_GL=egl
    export CUDA_HOME=/usr/local/cuda-12.2
    export PATH=/usr/local/cuda-12.2/bin:$PATH
    export LD_LIBRARY_PATH=/usr/local/cuda-12.2/lib64:${LD_LIBRARY_PATH:-}
else
    # Install OSMesa for CPU-based rendering
    apt-get install -y libosmesa6-dev

    # Set up environment for CPU
    cat >> /etc/environment << 'ENVEOF'
MUJOCO_GL=osmesa
ENVEOF

    export MUJOCO_GL=osmesa
fi

# Create simup Python environment
python3 -m venv /opt/simup/venv
source /opt/simup/venv/bin/activate

# Install MuJoCo and simulation stack
pip install --upgrade pip

if [ "$HAS_GPU" = true ]; then
    JAX_PACKAGE="jax[cuda12]==0.4.35"
else
    JAX_PACKAGE="jax==0.4.35"
fi

pip install \
    mujoco>=3.2 \
    mujoco-mjx>=3.2 \
    "${JAX_PACKAGE}" \
    flax \
    optax \
    dm-control \
    gymnasium[mujoco] \
    jupyterlab \
    ipywidgets \
    matplotlib \
    mediapy \
    tqdm \
    rich

# Clone the one-click-mujoco-azure repo (includes robot demo, examples, notebooks)
echo "=== Deploying simulation files ==="
git clone --depth 1 https://github.com/Haptic-AI/one-click-mujoco-azure.git /opt/simup/repo 2>/dev/null || true

# Deploy robot simulation files
mkdir -p /opt/simup/robot
if [ -d /opt/simup/repo/robot ]; then
    cp -r /opt/simup/repo/robot/* /opt/simup/robot/
    echo "Robot simulation files deployed from repo"
fi

# Deploy examples
mkdir -p /opt/simup/examples
if [ -d /opt/simup/repo/examples ]; then
    cp -r /opt/simup/repo/examples/* /opt/simup/examples/
    echo "Example scripts deployed from repo"
fi

# Clone MuJoCo playground examples
git clone --depth 1 https://github.com/google-deepmind/mujoco_playground.git /opt/simup/mujoco_playground 2>/dev/null || true

# Set up notebooks directory with humanoid demo as the default notebook
mkdir -p /opt/simup/notebooks
if [ -f /opt/simup/robot/humanoid_demo.ipynb ]; then
    cp /opt/simup/robot/humanoid_demo.ipynb /opt/simup/notebooks/
    echo "Humanoid demo notebook deployed"
fi

# Run a quick simulation test to verify everything works
echo "=== Running simulation test ==="
MUJOCO_GL=${MUJOCO_GL} /opt/simup/venv/bin/python /opt/simup/robot/simulate.py --duration 2 || echo "Simulation test failed (non-fatal)"

# Render a test frame using dm_control's built-in humanoid
MUJOCO_GL=${MUJOCO_GL} /opt/simup/venv/bin/python -c "
import mujoco, mediapy, os
import dm_control.suite as suite
suite_dir = os.path.dirname(suite.__file__)
model = mujoco.MjModel.from_xml_path(os.path.join(suite_dir, 'humanoid.xml'))
data = mujoco.MjData(model)
mujoco.mj_forward(model, data)
renderer = mujoco.Renderer(model, height=720, width=1280)
renderer.update_scene(data)
mediapy.write_image('/opt/simup/notebooks/humanoid_preview.png', renderer.render())
print('Humanoid preview rendered')
" || echo "Render test failed (non-fatal)"

# Generate a random Jupyter authentication token
JUPYTER_TOKEN=$(openssl rand -hex 24)
echo "$JUPYTER_TOKEN" > /opt/simup/.jupyter-token
chmod 600 /opt/simup/.jupyter-token

# Configure Jupyter Lab
ADMIN_USER=$(ls /home/ | head -1)
ADMIN_HOME="/home/${ADMIN_USER}"
mkdir -p "${ADMIN_HOME}/.jupyter"
cat > "${ADMIN_HOME}/.jupyter/jupyter_lab_config.py" << JCEOF
c.ServerApp.ip = '0.0.0.0'
c.ServerApp.port = 8888
c.ServerApp.open_browser = False
c.ServerApp.allow_root = True
c.ServerApp.token = '${JUPYTER_TOKEN}'
c.ServerApp.password = ''
c.ServerApp.allow_origin = '*'
c.ServerApp.root_dir = '/opt/simup/notebooks'
JCEOF
chown -R "${ADMIN_USER}:${ADMIN_USER}" "${ADMIN_HOME}/.jupyter"

# Ensure admin user owns all simup files
chown -R "${ADMIN_USER}:${ADMIN_USER}" /opt/simup

# Create systemd service for Jupyter
if [ "$HAS_GPU" = true ]; then
cat > /etc/systemd/system/jupyter.service << SVCEOF
[Unit]
Description=simup Jupyter Lab
After=network.target

[Service]
Type=simple
User=${ADMIN_USER}
ExecStart=/opt/simup/venv/bin/jupyter lab --config=${ADMIN_HOME}/.jupyter/jupyter_lab_config.py
Environment=MUJOCO_GL=egl
Environment=PATH=/opt/simup/venv/bin:/usr/local/cuda-12.2/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=LD_LIBRARY_PATH=/usr/local/cuda-12.2/lib64
WorkingDirectory=/opt/simup/notebooks
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF
else
cat > /etc/systemd/system/jupyter.service << SVCEOF
[Unit]
Description=simup Jupyter Lab
After=network.target

[Service]
Type=simple
User=${ADMIN_USER}
ExecStart=/opt/simup/venv/bin/jupyter lab --config=${ADMIN_HOME}/.jupyter/jupyter_lab_config.py
Environment=MUJOCO_GL=osmesa
Environment=PATH=/opt/simup/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WorkingDirectory=/opt/simup/notebooks
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF
fi

systemctl daemon-reload
systemctl enable jupyter
systemctl start jupyter

# Mark setup complete
touch "$SETUP_MARKER"
echo "=== simup setup completed at $(date) ==="
echo "=== dm_control humanoid loaded and ready ==="
echo "=== Jupyter Lab running on port 8888 ==="
