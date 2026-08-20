# Go2 safety test scenarios

This workspace contains reusable robot code in `src/`, executable scenarios in
`scripts/`, and model files in `weights/`.

## Reproduce the environment on another PC

Use Conda with `environment.yml` to reproduce the workspace on another Linux
PC. The repo keeps two Python requirement files:

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

For GPU tracking, verify that the PyTorch version you install
matches the CUDA driver on the new PC. CPU tracking also works by changing
`tracking.device` in `config.yaml` from `"0"` to `"cpu"`.

The person-tracking program requires this model file:

```text
weights/yolo11n-seg.pt
```

The repository is configured to include that specific model file in Git. If
the repository was copied without large files, copy the model into `weights/`
before running `scripts/tracking.py`.

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
