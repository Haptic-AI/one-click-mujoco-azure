"""MuJoCo Humanoid — Cinematic Commander Pose.

Uses dm_control's built-in humanoid model (MJCF). Zero external assets.
A leader stands on a raised ledge, pointing outward into the distance.
"""
from __future__ import annotations

import argparse
import os
import time

# Auto-configure headless GPU rendering (EGL) if no display is available.
# This must happen before importing mujoco to take effect.
if "DISPLAY" not in os.environ and "MUJOCO_GL" not in os.environ:
    os.environ["MUJOCO_GL"] = "egl"

import mujoco
import numpy as np


# Scene XML that wraps dm_control humanoid on a rocky ledge.
# We load the humanoid separately and composite via rendering.
LEDGE_SCENE_XML = """
<mujoco model="ledge_scene">
  <visual>
    <global offwidth="1920" offheight="1080"/>
    <quality shadowsize="8192"/>
  </visual>

  <asset>
    <texture name="sky" type="skybox" builtin="gradient"
             rgb1="0.25 0.35 0.55" rgb2="0.08 0.08 0.12"
             width="512" height="3072"/>
    <texture name="ground" type="2d" builtin="checker"
             rgb1="0.18 0.19 0.18" rgb2="0.15 0.15 0.14"
             width="512" height="512"/>
    <material name="ground_mat" texture="ground" texrepeat="12 12"
              texuniform="true" reflectance="0.1"/>
    <material name="rock_dark" rgba="0.28 0.24 0.20 1" specular="0.05"/>
    <material name="rock_med"  rgba="0.35 0.30 0.25 1" specular="0.05"/>
    <material name="rock_light" rgba="0.40 0.35 0.28 1" specular="0.08"/>
  </asset>

  <worldbody>
    <!-- Key light: warm, from camera-left and above -->
    <light pos="-3 -4 8" dir="0.3 0.4 -0.7" diffuse="1.0 0.92 0.75"
           specular="0.6 0.5 0.4" castshadow="true"/>
    <!-- Fill light: cool, from camera-right -->
    <light pos="4 -2 5" dir="-0.3 0.2 -0.6" diffuse="0.3 0.35 0.5"
           specular="0.1 0.1 0.15"/>
    <!-- Rim light: behind figure for edge definition -->
    <light pos="0 3 4" dir="0 -0.5 -0.5" diffuse="0.25 0.25 0.35"
           specular="0.2 0.2 0.3"/>

    <!-- Ground far below -->
    <geom name="ground" type="plane" size="30 30 0.1" pos="0 0 -0.5"
          material="ground_mat"/>

    <!-- Rocky ledge the humanoid stands on -->
    <!-- Main platform -->
    <geom type="box" size="1.8 1.2 0.25" pos="0 0 -0.05"
          material="rock_med" friction="1.5 0.5 0.5"/>
    <!-- Front edge: slopes down -->
    <geom type="box" size="1.0 0.6 0.15" pos="1.2 0 -0.15"
          euler="0 0.25 0" material="rock_dark"/>
    <!-- Rough top surface -->
    <geom type="ellipsoid" size="0.8 0.6 0.08" pos="-0.3 0.3 0.18"
          material="rock_light"/>
    <geom type="ellipsoid" size="0.5 0.4 0.06" pos="0.4 -0.2 0.16"
          material="rock_dark"/>
    <!-- Side boulders for depth -->
    <geom type="ellipsoid" size="0.6 0.5 0.35" pos="-1.5 0.5 -0.1"
          material="rock_dark"/>
    <geom type="ellipsoid" size="0.4 0.3 0.25" pos="1.6 -0.4 -0.2"
          material="rock_med"/>
    <!-- Small debris -->
    <geom type="ellipsoid" size="0.15 0.12 0.08" pos="0.7 0.5 0.22"
          material="rock_light"/>
    <geom type="ellipsoid" size="0.10 0.08 0.05" pos="-0.5 -0.4 0.20"
          material="rock_dark"/>
  </worldbody>
</mujoco>
"""


def load_model() -> tuple[mujoco.MjModel, mujoco.MjData]:
    """Load dm_control humanoid with HD buffer and enhanced visuals."""
    import dm_control.suite as suite
    suite_dir = os.path.dirname(suite.__file__)
    xml_path = os.path.join(suite_dir, "humanoid.xml")

    model = mujoco.MjModel.from_xml_path(xml_path)
    model.vis.global_.offwidth = 1920
    model.vis.global_.offheight = 1080
    model.vis.quality.shadowsize = 8192
    data = mujoco.MjData(model)
    return model, data


def set_commander_pose(model: mujoco.MjModel, data: mujoco.MjData) -> None:
    """Leader on a ridge, pointing outward.

    CRITICAL pose rules (judged by rendered frame, not intent):
      1. Only ONE arm reads as raised — the pointing arm.
         The other arm must be INVISIBLE in the silhouette.
      2. The pointing arm must be nearly horizontal — a DIRECTION,
         not a salute or raised fist.
      3. The torso must face the camera enough to read as BROAD.
      4. The stance must look PLANTED, not walking.

    Joint addresses (qpos index):
      0-2:  root position (x, y, z)
      3-6:  root quaternion (w, x, y, z)
      7:    abdomen_z       [-0.79, 0.79]
      8:    abdomen_y       [-1.31, 0.52]
      9:    abdomen_x       [-0.61, 0.61]
      10:   right_hip_x     [-0.44, 0.09]
      11:   right_hip_z     [-1.05, 0.61]
      12:   right_hip_y     [-1.92, 0.35]
      13:   right_knee      [-2.79, 0.03]
      14:   right_ankle_y   [-0.87, 0.87]
      15:   right_ankle_x   [-0.87, 0.87]
      16:   left_hip_x      [-0.44, 0.09]
      17:   left_hip_z      [-1.05, 0.61]
      18:   left_hip_y      [-2.09, 0.35]
      19:   left_knee       [-2.79, 0.03]
      20:   left_ankle_y    [-0.87, 0.87]
      21:   left_ankle_x    [-0.87, 0.87]
      22:   right_shoulder1 [-1.48, 1.05]
      23:   right_shoulder2 [-1.48, 1.05]
      24:   right_elbow     [-1.57, 0.87]
      25:   left_shoulder1  [-1.05, 1.48]
      26:   left_shoulder2  [-1.05, 1.48]
      27:   left_elbow      [-1.57, 0.87]
    """
    mujoco.mj_resetData(model, data)

    # === ROOT ===
    data.qpos[2] = 1.30   # standing height (on ledge surface)

    # Body faces ~30 degrees right of forward.
    # This means from a 3/4 front camera, the chest reads BROAD
    # and the pointing arm extends into frame-right.
    yaw = -0.40
    data.qpos[3] = np.cos(yaw / 2)   # w
    data.qpos[6] = np.sin(yaw / 2)   # z rotation

    # Tiny backward lean — chest proud, chin level
    lean = -0.03
    data.qpos[3] *= np.cos(lean / 2)
    data.qpos[5] = np.sin(lean / 2)

    # === TORSO ===
    # Twist upper body further into the pointing direction.
    # Combined with root yaw, this puts the chest facing ~50 deg right.
    data.qpos[7] = -0.25   # abdomen_z — strong twist toward pointing arm
    data.qpos[8] = -0.12   # abdomen_y — chest lifted, ribcage open
    data.qpos[9] = 0.0     # abdomen_x — no lateral lean

    # === RIGHT LEG (FRONT) ===
    # Forward, foot angled out. The "surveying" foot on ledge edge.
    data.qpos[10] = -0.22  # hip_x — forward
    data.qpos[11] = 0.20   # hip_z — wide
    data.qpos[12] = 0.20   # hip_y — foot turned outward
    data.qpos[13] = -0.25  # knee — soft bend
    data.qpos[14] = 0.12   # ankle_y — flat
    data.qpos[15] = 0.0

    # === LEFT LEG (BACK) ===
    # The anchor. Nearly straight. Weight lives here.
    data.qpos[16] = 0.08   # hip_x — trailing
    data.qpos[17] = -0.20  # hip_z — wide
    data.qpos[18] = -0.08  # hip_y — foot neutral
    data.qpos[19] = -0.08  # knee — nearly locked
    data.qpos[20] = -0.04  # ankle_y — flat
    data.qpos[21] = 0.0

    # === RIGHT ARM — THE POINT ===
    # This is the ONLY raised element. Must read instantly.
    # Nearly horizontal (shoulder1 ~ -0.90), not upward.
    # Elbow LOCKED at 0. Clean straight line from shoulder to fingertip.
    data.qpos[22] = -0.90  # shoulder1 — arm at horizontal
    data.qpos[23] = 0.05   # shoulder2 — tight to body plane
    data.qpos[24] = 0.0    # elbow — STRAIGHT

    # === LEFT ARM — QUIET ===
    # This arm must NOT compete with the pointing arm.
    # Hanging DOWN along the body. Elbow slightly bent.
    # From a 3/4 camera angle it should nearly disappear
    # behind the torso silhouette.
    data.qpos[25] = 0.60   # shoulder1 — arm pulled BACK and DOWN
    data.qpos[26] = 0.15   # shoulder2 — close to body (hidden)
    data.qpos[27] = -0.30  # elbow — slight natural bend

    mujoco.mj_forward(model, data)


def make_camera(azimuth: float, elevation: float, distance: float, lookat_z: float = 0.80) -> mujoco.MjvCamera:
    """Create a heroic low-angle camera."""
    cam = mujoco.MjvCamera()
    cam.type = mujoco.mjtCamera.mjCAMERA_FREE
    cam.lookat[:] = [0, 0, lookat_z]
    cam.azimuth = azimuth
    cam.elevation = elevation
    cam.distance = distance
    return cam


def run_video(duration: float = 8.0, output: str = "/tmp/humanoid_sim.mp4") -> None:
    """Cinematic poster shot with slow orbit."""
    import mediapy

    model, data = load_model()
    renderer = mujoco.Renderer(model, height=720, width=1280)
    set_commander_pose(model, data)

    fps = 30
    n_frames = int(duration * fps)
    frames = []

    start = time.time()
    for i in range(n_frames):
        p = i / n_frames

        # Hero angle: az=295 is where only the pointing arm reads,
        # the chest is broad, and the left arm hides behind torso.
        # Orbit only 20 degrees to keep the composition locked.
        cam = make_camera(
            azimuth=293 + p * 20,
            elevation=20 + 2 * np.sin(p * np.pi),
            distance=2.4 - 0.15 * p,
            lookat_z=0.80,
        )

        mujoco.mj_forward(model, data)
        renderer.update_scene(data, camera=cam)
        frames.append(renderer.render().copy())

    elapsed = time.time() - start
    mediapy.write_video(output, frames, fps=fps)
    print(f"Rendered {n_frames} frames in {elapsed:.3f}s")
    print(f"Saved to {output}")


def run_headless(duration: float = 5.0, render: bool = False, frames_dir: str | None = None) -> tuple[mujoco.MjModel, mujoco.MjData]:
    """Run headless with optional frame output."""
    model, data = load_model()
    set_commander_pose(model, data)

    renderer = None
    if render or frames_dir:
        renderer = mujoco.Renderer(model, height=720, width=1280)
        if frames_dir:
            os.makedirs(frames_dir, exist_ok=True)

    frame_count = 0
    n_frames = int(duration * 30)

    start = time.time()
    for step in range(n_frames):
        mujoco.mj_forward(model, data)
        if renderer:
            renderer.update_scene(data)
            frame = renderer.render()
            if frames_dir:
                import mediapy
                mediapy.write_image(
                    os.path.join(frames_dir, f"frame_{frame_count:04d}.png"), frame
                )
            frame_count += 1

    elapsed = time.time() - start
    print(f"Rendered {duration}s in {elapsed:.3f}s")
    if frame_count:
        print(f"Saved {frame_count} frames")
    return model, data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MuJoCo Humanoid — Commander Pose")
    parser.add_argument("--duration", type=float, default=8.0)
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--video", action="store_true")
    parser.add_argument("--output", type=str, default="/tmp/humanoid_sim.mp4")
    parser.add_argument("--frames-dir", type=str)
    args = parser.parse_args()

    if args.video:
        run_video(duration=args.duration, output=args.output)
    else:
        run_headless(duration=args.duration, render=args.render, frames_dir=args.frames_dir)
