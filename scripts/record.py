"""Record the Go2 front camera to a timestamped MP4 file."""

import argparse
import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path

import cv2
from aiortc.mediastreams import MediaStreamError
from unitree_webrtc_connect import UnitreeWebRTCConnection, WebRTCConnectionMethod

# The Go2 may send inter-frame H.264 packets before its first keyframe after
# enabling the video channel. aiortc safely drops these transient packets.
logging.getLogger("aiortc.codecs.h264").setLevel(logging.ERROR)

import _bootstrap  # noqa: F401
from src.common_api import CONNECTION_CONFIG, ROBOT_IP
from src.config import CONFIG_PATH, section

RECORDING_CONFIG = section("recording")
WORKSPACE_DIR = CONFIG_PATH.parent


def output_path(name=None):
    """Build a timestamped output path below the configured directory."""
    directory = Path(RECORDING_CONFIG["directory"])
    if not directory.is_absolute():
        directory = WORKSPACE_DIR / directory
    directory.mkdir(parents=True, exist_ok=True)
    stem = Path(name).stem if name else "go2"
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return directory / f"{stem}_{timestamp}.mp4"


async def record(name=None):
    """Connect to the camera and record frames until q, Esc, or Ctrl+C."""
    output = output_path(name)
    fps = float(RECORDING_CONFIG["fps"])
    preview = bool(RECORDING_CONFIG["preview"])
    stop_event = asyncio.Event()
    camera_ready = asyncio.Event()
    video_done = asyncio.Event()
    writer = None
    frames = 0
    started = None
    connection = UnitreeWebRTCConnection(
        WebRTCConnectionMethod.LocalSTA,
        ip=ROBOT_IP,
        aes_128_key=CONNECTION_CONFIG.get("aes_128_key") or None,
    )

    async def receive(track):
        nonlocal writer, frames, started
        try:
            while not stop_event.is_set():
                try:
                    frame = await track.recv()
                except MediaStreamError:
                    stop_event.set()
                    return
                image = frame.to_ndarray(format="bgr24")
                if writer is None:
                    height, width = image.shape[:2]
                    writer = cv2.VideoWriter(
                        str(output),
                        cv2.VideoWriter_fourcc(*"mp4v"),
                        fps,
                        (width, height),
                    )
                    if not writer.isOpened():
                        raise RuntimeError(f"Could not open MP4 writer: {output}")
                    started = time.monotonic()
                    print(f"Recording to {output} at {width}x{height}, {fps:g} FPS")
                    camera_ready.set()
                writer.write(image)
                frames += 1
                if preview:
                    cv2.imshow("Go2 Front Camera - RECORDING", image)
                    if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                        stop_event.set()
        finally:
            video_done.set()

    connected = False
    try:
        print(f"Connecting to Go2 camera at {ROBOT_IP}...")
        await connection.connect()
        connected = True
        connection.video.add_track_callback(receive)
        connection.video.switchVideoChannel(True)
        print("Waiting for the Go2 camera to deliver its first frame...", flush=True)
        await asyncio.wait_for(camera_ready.wait(), timeout=20)
        print("Go2 camera connected. Press q, Esc, or Ctrl+C to stop recording.")
        await stop_event.wait()
    finally:
        stop_event.set()
        if connected:
            connection.video.switchVideoChannel(False)
            await connection.disconnect()
        if not video_done.is_set():
            try:
                await asyncio.wait_for(video_done.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
        if writer is not None:
            writer.release()
        cv2.destroyAllWindows()
        if started is not None:
            print(
                f"Saved {output} ({frames} frames, "
                f"{time.monotonic() - started:.1f} seconds)"
            )
        else:
            print("No camera frames were recorded.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("name", nargs="?", help="optional recording name")
    try:
        asyncio.run(record(parser.parse_args().name))
    except KeyboardInterrupt:
        print("\nRecording interrupted")
