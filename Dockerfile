FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

# Prevent Python cache files and make terminal logs appear immediately.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/opt/venvs/main/bin:/usr/local/bin:/usr/bin:/bin

# Runtime libraries are needed by OpenCV, camera/video encoding, WebRTC audio,
# and MediaPipe. build-essential is needed when a package has no binary wheel.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        portaudio19-dev \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# One image contains two isolated Python environments. The main environment
# uses CUDA PyTorch for YOLO. The MediaPipe environment keeps NumPy below 2 and
# cannot accidentally replace the tracking environment's OpenCV/NumPy stack.
COPY requirements-main.txt requirements-mediapipe.txt ./
RUN python -m venv /opt/venvs/main \
    && python -m venv /opt/venvs/mediapipe \
    && /opt/venvs/main/bin/python -m pip install --upgrade pip \
    && /opt/venvs/main/bin/python -m pip install \
        --index-url https://download.pytorch.org/whl/cu128 \
        torch==2.11.0 torchvision==0.26.0 \
    && /opt/venvs/main/bin/python -m pip install -r requirements-main.txt \
    && /opt/venvs/mediapipe/bin/python -m pip install --upgrade pip \
    && /opt/venvs/mediapipe/bin/python -m pip install -r requirements-mediapipe.txt

COPY config.yaml ./
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY weights/ ./weights/

# The main GPU environment is the default. Docker Compose changes PATH for the
# gesture service so `python` automatically refers to the MediaPipe environment.
CMD ["bash"]
