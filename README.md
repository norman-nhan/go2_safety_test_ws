# Go2 safety test scenarios

This workspace contains reusable robot code in `src/`, executable scenarios in
`scripts/`, and model files in `weights/`.

## Reproduce the environment on another PC

There are two supported ways to reproduce the workspace on another Linux PC:

- Conda, using `environment.yml`
- Docker, using `compose.yaml`

For Python dependency reproduction outside Docker, the repo keeps two
environment files:

- `requirements-main.txt` for motion, recording, and GPU tracking
- `requirements-mediapipe.txt` for gesture detection

MediaPipe and the tracking stack need different NumPy/OpenCV combinations, so
they are intentionally split into two isolated environments.

On Ubuntu, first install Miniconda (or Anaconda), Git, and the system libraries
used by camera, audio, and OpenCV packages:

```bash
sudo apt update
sudo apt install -y git portaudio19-dev libgl1 libglib2.0-0
```

Clone or copy this repository, then create the Conda environment from the
workspace root:

```bash
cd ~/go2_safety_test_ws
conda env create -f environment.yml
conda activate go2sim
```

If the `go2sim` environment already exists, update it after pulling changes:

```bash
conda env update -n go2sim -f environment.yml --prune
```

For a plain Python virtual environment instead of Conda, create one or both of
the following depending on what you want to run:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-main.txt
```

If you want the gesture scripts in a separate environment, create another venv
and install `requirements-mediapipe.txt` there.

For GPU tracking outside Docker, verify that the PyTorch version you install
matches the CUDA driver on the new PC. CPU tracking also works by changing
`tracking.device` in `config.yaml` from `"0"` to `"cpu"`.

The person-tracking program requires this model file:

```text
weights/yolo11n-seg.pt
```

The repository is configured to include that specific model file in Git. If
the repository was copied without large files, copy the model into `weights/`
before running `scripts/tracking.py`.

## Docker installation

Docker stores the Python version, system libraries, and pinned Python packages
inside one image. That image contains two isolated virtual environments:

- `/opt/venvs/main`: motion, recording, and CUDA-accelerated YOLO tracking
- `/opt/venvs/mediapipe`: hand gestures with MediaPipe-compatible NumPy/OpenCV

Robot-specific values and generated recordings remain outside the image.

The repository keeps only two requirement files:

- `requirements-main.txt` for GPU motion, recording, and tracking
- `requirements-mediapipe.txt` for gesture detection

Each file includes the shared packages it needs, so there is no extra helper
requirements file to track.

The Docker image now starts from NVIDIA's official CUDA runtime family instead
of a generic Python base image. That keeps GPU support aligned with the host
NVIDIA toolkit and removes a lot of non-GPU base image baggage.

Install Docker Engine on the new Linux PC, then build the image from the
workspace root:

```bash
cd ~/go2_safety_test_ws
docker compose build
```

Create the local Docker variable file and set the correct robot IP:

```bash
cp .env.example .env
```

Edit `.env` so it contains the address assigned to the robot:

```text
GO2IP=10.0.0.61
```

`.env` is ignored by Git because it is specific to the current network.

The `go2` service selects the main GPU environment. Use it for P/K scenarios,
posture actions, recording, wet shaking, and tracking:

```bash
docker compose run --rm go2
```

Inside the container, run a scenario normally:

```bash
python scripts/p1.py
```

You can also run one scenario directly from the host:

```bash
docker compose run --rm go2 python scripts/p1.py
docker compose run --rm go2 python scripts/record.py participant_001
docker compose run --rm go2 python scripts/tracking.py
```

The `gesture` service selects the isolated MediaPipe environment. Use it for
C1, C2, and C3:

```bash
docker compose run --rm gesture python scripts/c1.py
docker compose run --rm gesture python scripts/c2.py
docker compose run --rm gesture python scripts/c3.py
```

The Compose configuration uses host networking because the computer and Go2
communicate over the local network. This configuration is intended for Linux;
Docker Desktop on Windows or macOS handles host networking differently.

### Camera preview from Docker

Camera and gesture scripts use OpenCV windows. On a Linux X11 desktop, allow
the local Docker user to open a window before starting the container:

```bash
xhost +local:docker
docker compose run --rm gesture python scripts/c2.py
xhost -local:docker
```

The last command removes the temporary display permission. For recording
without a GUI, set `recording.preview: false` in `config.yaml`. Recordings are
written through the Compose volume to the host's `recordings/` directory.

### NVIDIA GPU tracking from Docker

The main environment installs PyTorch 2.11 with CUDA 12.8 support, and the
`go2` Compose service requests all available NVIDIA GPUs. Install both the
NVIDIA driver and NVIDIA Container Toolkit on the host before running it.

Verify GPU access without connecting to the robot:

```bash
docker compose run --rm go2 python -c \
  "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

`torch.cuda.is_available()` must print `True`. Keep tracking configured for the
first GPU:

```yaml
tracking:
  device: "0"
```

To run the main service without an NVIDIA GPU, remove `gpus: all` from the
`go2` service and change the tracking device to CPU:

```yaml
tracking:
  device: "cpu"
```

The MediaPipe environment remains CPU-based because its purpose is dependency
isolation. YOLO tracking, the expensive neural-network workload, uses the GPU.

## Configure the robot connection

Open `setup.sh` and set `GO2IP` to the Go2 robot's current address:

```bash
export GO2IP="10.0.0.61"
```

The value is machine/network specific. Do not copy the example IP unchanged
unless it is actually the address assigned to your robot.

If the robot firmware requires an AES connection key, set
`connection.aes_128_key` in `config.yaml`. Keep it empty for firmware that does
not require a key, and do not commit a private robot key to a public repository.

## Start a session

Run commands from the workspace root:

```bash
cd ~/go2_safety_test_ws
conda activate go2sim
source setup.sh
```

Run `source setup.sh` again in every new terminal. Environment variables set by
one terminal are not automatically available in another terminal.

Before running any movement scenario, put the robot on a clear, level surface
and keep the remote emergency stop ready. Each program requires typing `MOVE`
before it sends robot commands.

## Camera and gesture scenarios

The C scenarios use MediaPipe Hands from `src/gesture_api.py`. The
`weights/yolo11n-seg.pt` model is not used for hand gestures; it is a general
YOLO object/person segmentation model.

```bash
python scripts/c1.py
```

Waits for `WAVING`, waits another 10 seconds, then performs a short forward
movement.

```bash
python scripts/c2.py
```

Moves forward immediately and stops 0.7 seconds after recognizing a steady
open palm as `STOP`. It also stops at the `scenarios.c2.max_move_seconds`
timeout configured in `config.yaml`.

```bash
python scripts/c3.py
```

Waits for a steady open-palm `STOP` gesture and then performs a short forward
movement.

Test gesture tuning with the short C2 timeout in `config.yaml` before allowing
longer movement.

## Posture and body-motion scenarios

```bash
python scripts/crouch.py
```

Stops active movement and enters Unitree's supported `StandDown` posture.

```bash
python scripts/standup.py
```

Uses `StandUp`, waits for the transition, and then enables `BalanceStand`.

```bash
python scripts/nodding.py
```

Nods the fixed head by gently oscillating torso pitch, then restores neutral.

```bash
python scripts/say_no.py
```

Shakes the fixed head left and right by oscillating torso yaw, then restores
neutral.

```bash
python scripts/wetshaking.py
```

Performs a wet-dog-style torso-roll motion and always restores neutral. Use a
flat, dry, non-slip floor and start with conservative settings.

## Camera utilities

```bash
python scripts/record.py
python scripts/record.py participant_001
```

Records the front camera to the configured `recordings/` directory. The
optional name is included in the timestamped MP4 filename. Press `q`, `Esc`, or
`Ctrl+C` to finish and close the file.

```bash
python scripts/tracking.py
```

Loads `weights/yolo11n-seg.pt`, selects the largest visible person, rotates to
center them, and moves forward/backward to maintain the configured apparent
size. Tracking routes its velocity through Unitree's obstacle-avoidance API
instead of sending direct sport `Move` commands. The current configuration
allows backward tracking; only use it when the rear path is clear and monitored
because rear obstacle coverage should not be assumed. Tracking causes
autonomous motion and requires typing `TRACK`.

## Configuration

`setup.sh` contains only the robot network address:

```bash
export GO2IP="10.0.0.61"
```

Set this to the correct IP address for your robot.

All other shared settings are in `config.yaml`:

- `connection`: optional WebRTC AES key
- `gesture_movement`: C1/C3 movement speed, duration, and cooldown
- `hand_detection`: MediaPipe confidence and STOP/WAVING thresholds
- `scenarios.c2`: C2 speed, post-detection delay, and fail-safe timeout
- `scenarios.body_motion`: nod, head-shake, and wet-shake parameters
- `recording`: MP4 output directory, FPS, and preview setting
- `tracking`: YOLO model, inference, centering, distance, and speed settings

For example, to make C2's fail-safe timeout 10 seconds, edit:

```yaml
scenarios:
  c2:
    max_move_seconds: 10.0
```

Restart the Python program after changing `config.yaml`; configuration is
loaded when the program starts.

## Stopping a program

Use the remote emergency stop whenever robot motion becomes unsafe. For normal
program shutdown, press `q` or `Esc` in the camera window, or press `Ctrl+C` in
the terminal.
